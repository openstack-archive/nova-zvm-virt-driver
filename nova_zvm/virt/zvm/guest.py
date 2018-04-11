# Copyright 2017,2018 IBM Corp.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import eventlet
import os
import six
import time

from oslo_concurrency import lockutils
from oslo_log import log as logging
from oslo_utils import excutils

from nova.compute import power_state as compute_power_state
from nova.compute import task_states
from nova import conf
from nova import exception
from nova.image import glance
from nova.objects import fields as obj_fields
from nova import utils
from nova.virt import hardware
from nova.virt import images
from nova_zvm.virt.zvm import utils as zvmutils


LOG = logging.getLogger(__name__)
CONF = conf.CONF


DEFAULT_EPH_DISK_FMT = 'ext3'
ZVM_POWER_STATE = {
    'on': compute_power_state.RUNNING,
    'off': compute_power_state.SHUTDOWN,
    }


class Guest(object):
    """z/VM implementation of ComputeDriver."""

    def __init__(self, hypervisor, instance, virtapi=None):
        super(Guest, self).__init__()

        self.virtapi = virtapi
        self._hypervisor = hypervisor
        self._instance = instance

    def _mapping_power_state(self, power_state):
        """Translate power state to OpenStack defined constants."""
        return ZVM_POWER_STATE.get(power_state, compute_power_state.NOSTATE)

    def get_info(self):
        """Get the current status of an instance."""
        power_state = self._mapping_power_state(
            self._hypervisor.guest_get_power_state(
                self._instance.name))
        return hardware.InstanceInfo(power_state)

    def spawn(self, context, image_meta, injected_files,
              admin_password, allocations, network_info=None,
              block_device_info=None, flavor=None):

        LOG.info("Spawning new instance %s on zVM hypervisor",
                 self._instance.name, instance=self._instance)

        if self._exists_in_hypervisor():
            raise exception.InstanceExists(name=self._instance.name)

        try:
            spawn_start = time.time()
            os_distro = image_meta.properties.os_distro
            transportfiles = zvmutils.generate_configdrive(context,
                self._instance, injected_files, admin_password)

            resp = self._get_image_info(context, image_meta.id, os_distro)
            spawn_image_name = resp[0]['imagename']
            disk_list, eph_list = self._set_disk_list(self._instance,
                                                      spawn_image_name,
                                                      block_device_info)

            # Create the guest vm
            self._hypervisor.guest_create(self._instance.name,
                self._instance.vcpus, self._instance.memory_mb,
                disk_list)

            # Deploy image to the guest vm
            self._hypervisor.guest_deploy(self._instance.name,
                spawn_image_name, transportfiles=transportfiles)

            # Handle ephemeral disks
            if eph_list:
                self._hypervisor.guest_config_minidisks(self._instance.name,
                                                        eph_list)
            # Setup network for z/VM instance
            self._wait_vif_plug_events(self._instance.name, os_distro,
                network_info, self._instance)

            self._hypervisor.guest_start(self._instance.name)
            spawn_time = time.time() - spawn_start
            LOG.info("Instance spawned successfully in %s seconds",
                     spawn_time, instance=self._instance)
        except Exception as err:
            with excutils.save_and_reraise_exception():
                LOG.error("Deploy  instance %(instance)s "
                          "failed with reason: %(err)s",
                          {'instance': self._instance.name, 'err': err},
                          instance=self._instance)
                try:
                    self.destroy(context, self._instance, network_info,
                                 block_device_info)
                except Exception as err:
                    LOG.exception("Failed to destroy instance",
                                  instance=self._instance)

    @lockutils.synchronized('IMAGE_INFO_SEMAPHORE')
    def _get_image_info(self, context, image_meta_id, os_distro):
        try:
            return self._hypervisor.image_query(imagename=image_meta_id)
        except exception.NovaException as err:
            with excutils.save_and_reraise_exception() as sare:
                if err.kwargs['results']['overallRC'] == 404:
                    sare.reraise = False
                    self._import_spawn_image(context, image_meta_id, os_distro)
                    return self._hypervisor.image_query(
                        imagename=image_meta_id)

    def _set_disk_list(self, instance, image_name, block_device_info):
        if instance.root_gb == 0:
            root_disk_size = self._hypervisor.image_get_root_disk_size(
                image_name)
        else:
            root_disk_size = '%ig' % instance.root_gb

        disk_list = []
        root_disk = {'size': root_disk_size,
                     'is_boot_disk': True
                    }
        disk_list.append(root_disk)
        ephemeral_disks_info = block_device_info.get('ephemerals', [])
        eph_list = []
        for eph in ephemeral_disks_info:
            eph_dict = {'size': '%ig' % eph['size'],
                        'format': (eph['guest_format'] or
                                   CONF.default_ephemeral_format or
                                   DEFAULT_EPH_DISK_FMT)}
            eph_list.append(eph_dict)

        if eph_list:
            disk_list.extend(eph_list)
        return disk_list, eph_list

    def _setup_network(self, vm_name, os_distro, network_info, instance):
        LOG.debug("Creating NICs for vm %s", vm_name)
        inst_nets = []
        for vif in network_info:
            subnet = vif['network']['subnets'][0]
            _net = {'ip_addr': subnet['ips'][0]['address'],
                    'gateway_addr': subnet['gateway']['address'],
                    'cidr': subnet['cidr'],
                    'mac_addr': vif['address'],
                    'nic_id': vif['id']}
            inst_nets.append(_net)

        if inst_nets:
            self._hypervisor.guest_create_network_interface(vm_name,
                os_distro, inst_nets)

    def _get_neutron_event(self, instance, network_info):
        if utils.is_neutron() and CONF.vif_plugging_timeout:
            return [('network-vif-plugged', vif['id'])
                    for vif in network_info if vif.get('active') is False]
        else:
            return []

    def _neutron_failed_callback(self, event_name, instance):
        LOG.error("Neutron Reported failure on event %s for instance",
                  event_name, instance=instance)
        if CONF.vif_plugging_is_fatal:
            raise exception.VirtualInterfaceCreateException()

    def _wait_vif_plug_events(self, vm_name, os_distro, network_info,
                              instance):
        timeout = CONF.vif_plugging_timeout
        try:
            event = self._get_neutron_event(instance, network_info)
            with self.virtapi.wait_for_instance_event(
                    instance, event, deadline=timeout,
                    error_callback=self._neutron_failed_callback):
                self._setup_network(vm_name, os_distro, network_info, instance)
        except eventlet.timeout.Timeout:
            LOG.warning("Timeout waiting for vif plugging callback.",
                        instance=instance)
            if CONF.vif_plugging_is_fatal:
                raise exception.VirtualInterfaceCreateException()
        except Exception as err:
            LOG.error("Failed for vif plugging: %s", six.text_type(err),
                       instance=instance)
            raise

    def _import_spawn_image(self, context, image_meta_id, image_os_version):
        LOG.debug("Downloading the image %s from glance to nova compute "
                  "server", image_meta_id)
        image_path = os.path.join(os.path.normpath(CONF.zvm.image_tmp_path),
                                  image_meta_id)
        if not os.path.exists(image_path):
            images.fetch(context, image_meta_id, image_path)
        image_url = "file://" + image_path
        image_meta = {'os_version': image_os_version}
        self._hypervisor.image_import(image_meta_id, image_url, image_meta)

    def _exists_in_hypervisor(self):
        return self._hypervisor.guest_exists(self._instance)

    def destroy(self, context, network_info=None,
                block_device_info=None, destroy_disks=False):
        if self._exists_in_hypervisor():
            LOG.info("Destroying instance", instance=self._instance)
            try:
                self._hypervisor.guest_delete(self._instance.name)
            except exception.NovaException as err:
                if err.kwargs['results']['overallRC'] == 404:
                    LOG.info("guest disappear during destroy",
                             instance=self._instance)
                else:
                    raise
        else:
            LOG.warning("Instance does not exist", instance=self._instance)

    def snapshot(self, context, image_id, update_task_state):
        (image_service, image_id) = glance.get_remote_image_service(
                                                    context, image_id)

        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)

        instance = self._instance

        try:
            self._hypervisor.guest_capture(instance.name, image_id)
        except Exception as err:
            with excutils.save_and_reraise_exception():
                LOG.error("Failed to capture the instance "
                          "to generate an image with reason: %(err)s",
                          {'err': err}, instance=instance)
                # Clean up the image from glance
                image_service.delete(context, image_id)

        # Export the image to nova-compute server temporary
        image_path = os.path.join(os.path.normpath(
                            CONF.zvm.image_tmp_path), image_id)
        dest_path = "file://" + image_path
        try:
            resp = self._hypervisor.image_export(image_id, dest_path)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error("Failed to export image %s from SDK server to "
                          "nova compute server", image_id)
                image_service.delete(context, image_id)
                self._hypervisor.image_delete(image_id)

        # Save image to glance
        new_image_meta = {
            'is_public': False,
            'status': 'active',
            'properties': {
                 'image_location': 'snapshot',
                 'image_state': 'available',
                 'owner_id': instance['project_id'],
                 'os_distro': resp['os_version'],
                 'architecture': obj_fields.Architecture.S390X,
                 'hypervisor_type': obj_fields.HVType.ZVM,
            },
            'disk_format': 'raw',
            'container_format': 'bare',
        }
        update_task_state(task_state=task_states.IMAGE_UPLOADING,
                          expected_state=task_states.IMAGE_PENDING_UPLOAD)

        # Save the image to glance
        try:
            with open(image_path, 'r') as image_file:
                image_service.update(context,
                                     image_id,
                                     new_image_meta,
                                     image_file,
                                     purge_props=False)
        except Exception:
            with excutils.save_and_reraise_exception():
                image_service.delete(context, image_id)
        finally:
            zvmutils.clean_up_file(image_path)
            self._hypervisor.image_delete(image_id)

        LOG.debug("Snapshot image upload complete", instance=instance)

    def guest_power_action(self, action, *args, **kwargs):
        self._hypervisor.guest_power_action(action, self._instance.name,
                                            *args, **kwargs)
