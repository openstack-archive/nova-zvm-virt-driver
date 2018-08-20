# Copyright 2013 IBM Corp.
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

import datetime
import eventlet
import os
import pwd
import time

from nova.compute import power_state
from nova.compute import task_states
from nova import exception
from nova.i18n import _
from nova.image import glance
from nova.objects import fields as obj_fields
from nova.virt import driver
from nova.virt import hardware
from nova.virt import images
from nova.volume import cinder
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_service import loopingcall
from oslo_utils import excutils
from oslo_utils import timeutils

from nova_zvm.virt.zvm import conf
from nova_zvm.virt.zvm import const
from nova_zvm.virt.zvm import utils as zvmutils


LOG = logging.getLogger(__name__)

CONF = conf.CONF


class ZVMDriver(driver.ComputeDriver):
    """z/VM implementation of ComputeDriver."""

    capabilities = {
        "has_imagecache": True,
        "supports_recreate": False,
        "supports_migrate_to_same_host": True,
        "supports_attach_interface": False
    }

    def __init__(self, virtapi):
        super(ZVMDriver, self).__init__(virtapi)
        self._reqh = zvmutils.zVMConnectorRequestHandler()
        self._vmutils = zvmutils.VMUtils()
        self._pathutils = zvmutils.PathUtils()
        self._imageop_semaphore = eventlet.semaphore.Semaphore(1)
        self._volume_api = cinder.API()

        # get hypervisor host name
        res = self._reqh.call('host_get_info')
        self._hypervisor_hostname = res['hypervisor_hostname']
        LOG.info("The zVM compute driver has been initialized.")

    def init_host(self, host):
        """Initialize anything that is necessary for the driver to function,
        including catching up with currently running VM's on the given host.
        """
        pass

    def list_instances(self):
        """Return the names of all the instances known to the virtualization
        layer, as a list.
        """
        return self._reqh.call('guest_list')

    def _get_host_status(self):
        LOG.debug("Getting host status for %s", CONF.host)

        info = self._reqh.call('host_get_info')

        host_status = {'host': CONF.host,
                       'allowed_vm_type': const.ALLOWED_VM_TYPE}
        host_status['vcpus'] = info['vcpus']
        host_status['vcpus_used'] = info['vcpus_used']
        host_status['cpu_info'] = info['cpu_info']
        host_status['disk_total'] = info['disk_total']
        host_status['disk_used'] = info['disk_used']
        host_status['disk_available'] = info['disk_available']
        host_status['host_memory_total'] = info['memory_mb']
        host_status['host_memory_free'] = (info['memory_mb'] -
                                           info['memory_mb_used'])
        host_status['hypervisor_type'] = info['hypervisor_type']
        host_status['hypervisor_version'] = info['hypervisor_version']
        host_status['hypervisor_hostname'] = info['hypervisor_hostname']
        host_status['supported_instances'] = [(const.ARCHITECTURE,
                                               const.HYPERVISOR_TYPE,
                                               obj_fields.VMMode.HVM)]
        host_status['ipl_time'] = info['ipl_time']

        return host_status

    def get_available_resource(self, nodename=None):
        LOG.debug("Getting available resource for %s", CONF.host)

        try:
            host_stats = self._reqh.call('host_get_info')
        except exception.NovaException:
            host_stats = {}

        res = {
            'vcpus': host_stats.get('vcpus', 0),
            'memory_mb': host_stats.get('memory_mb', 0),
            'local_gb': host_stats.get('disk_total', 0),
            'vcpus_used': 0,
            'memory_mb_used': host_stats.get('memory_mb_used', 0),
            'local_gb_used': host_stats.get('disk_used', 0),
            'hypervisor_type': host_stats.get('hypervisor_type', 'zvm'),
            'hypervisor_version': host_stats.get('hypervisor_version', ''),
            'hypervisor_hostname': host_stats.get('hypervisor_hostname', ''),
            'cpu_info': jsonutils.dumps(host_stats.get('cpu_info', {})),
            'disk_available_least': host_stats.get('disk_available', 0),
            'supported_instances': [(const.ARCHITECTURE,
                                     const.HYPERVISOR_TYPE,
                                     obj_fields.VMMode.HVM)],
            'numa_topology': None,
        }

        return res

    def get_available_nodes(self, refresh=False):
        return [self._hypervisor_hostname]

    def _mapping_power_stat(self, power_stat):
        """Translate power state to OpenStack defined constants."""
        return const.ZVM_POWER_STAT.get(power_stat, power_state.NOSTATE)

    def get_info(self, instance):
        """Get the current status of an instance."""
        power_stat = ''
        try:
            power_stat = self._reqh.call('guest_get_power_state',
                                         instance['name'])
        except exception.NovaException as err:
            if err.kwargs['results']['overallRC'] == 404:
                # instance not exists
                LOG.warning("Get power state of non-exist instance: %s",
                            instance['name'])
                raise exception.InstanceNotFound(instance_id=instance['name'])
            else:
                raise

        power_stat = self._mapping_power_stat(power_stat)
        _instance_info = hardware.InstanceInfo(power_stat)

        return _instance_info

    def _instance_exists(self, instance_name):
        """Overwrite this to using instance name as input parameter."""
        return instance_name.upper() in self.list_instances()

    def instance_exists(self, instance):
        """Overwrite this to using instance name as input parameter."""
        return self._instance_exists(instance.name)

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, allocations, network_info=None,
              block_device_info=None, flavor=None):
        LOG.info(_("Spawning new instance %s on zVM hypervisor"),
                 instance['name'], instance=instance)
        # For zVM instance, limit the maximum length of instance name to \ 8
        if len(instance['name']) > 8:
            msg = (_("Don't support spawn vm on zVM hypervisor with instance "
                "name: %s, please change your instance_name_template to make "
                "sure the length of instance name is not longer than 8 "
                "characters") % instance['name'])
            raise exception.InvalidInput(reason=msg)
        try:
            spawn_start = time.time()
            os_distro = image_meta.properties.os_distro
            transportfiles = self._vmutils.generate_configdrive(
                            context, instance, injected_files, admin_password)

            resp = self._get_image_info(context, image_meta.id, os_distro)
            spawn_image_name = resp[0]['imagename']
            disk_list, eph_list = self._set_disk_list(instance,
                                                      spawn_image_name,
                                                      block_device_info)

            # Create the guest vm
            self._reqh.call('guest_create', instance['name'],
                            instance['vcpus'], instance['memory_mb'],
                            disk_list=disk_list)

            # Deploy image to the guest vm
            remotehost = self._get_host()
            self._reqh.call('guest_deploy', instance['name'],
                            spawn_image_name, transportfiles=transportfiles,
                            remotehost=remotehost)

            # Setup network for z/VM instance
            self._setup_network(instance['name'], os_distro, network_info,
                                instance)

            # Handle ephemeral disks
            if eph_list:
                self._reqh.call('guest_config_minidisks',
                                instance['name'], eph_list)

            self._wait_network_ready(instance)

            self._reqh.call('guest_start', instance['name'])
            spawn_time = time.time() - spawn_start
            LOG.info(_("Instance spawned succeeded in %s seconds"),
                     spawn_time, instance=instance)
        except Exception as err:
            with excutils.save_and_reraise_exception():
                LOG.error(_("Deploy image to instance %(instance)s "
                            "failed with reason: %(err)s"),
                          {'instance': instance['name'], 'err': err},
                          instance=instance)
                self.destroy(context, instance, network_info,
                             block_device_info)

    def _get_image_info(self, context, image_meta_id, os_distro):
        spawn_image_exist = False
        try:
            spawn_image_exist = self._reqh.call('image_query',
                                                imagename=image_meta_id)
        except exception.NovaException as err:
            if err.kwargs['results']['overallRC'] == 404:
                # image not exist, nothing to do
                pass
            else:
                raise err

        if not spawn_image_exist:
            with self._imageop_semaphore:
                self._import_spawn_image(context, image_meta_id, os_distro)
            return self._reqh.call('image_query', imagename=image_meta_id)
        else:
            return spawn_image_exist

    def _set_disk_list(self, instance, image_name, block_device_info):
        if instance['root_gb'] == 0:
            root_disk_size = self._reqh.call('image_get_root_disk_size',
                                             image_name)
        else:
            root_disk_size = '%ig' % instance['root_gb']

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
                                   const.DEFAULT_EPH_DISK_FMT)}
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
            self._reqh.call('guest_create_network_interface',
                            vm_name, os_distro, inst_nets)

    def _wait_network_ready(self, instance):
        """Wait until neutron zvm-agent add all NICs to vm"""
        inst_name = instance['name']

        def _wait_for_nics_add_in_vm(inst_name, expiration):
            if (CONF.zvm_reachable_timeout and
                    timeutils.utcnow() > expiration):
                msg = _("NIC update check failed "
                        "on instance:%s") % instance.uuid
                raise exception.NovaException(message=msg)

            try:
                switch_dict = self._reqh.call('guest_get_nic_vswitch_info',
                                              inst_name)
                if switch_dict and None not in switch_dict.values():
                    for key, value in switch_dict.items():
                        user_direct = self._reqh.call(
                                            'guest_get_definition_info',
                                            inst_name)
                        if not self._nic_coupled(user_direct, key, value):
                            return
                else:
                    # In this case, the nic switch info is not ready yet
                    # need another loop to check until time out or find it
                    return

            except Exception as e:
                # Ignore any zvm driver exceptions
                LOG.info(_('encounter error %s during get vswitch info'),
                         e.format_message(), instance=instance)
                return

            # Enter here means all NIC granted
            LOG.info(_("All NICs are added in user direct for "
                         "instance %s."), inst_name, instance=instance)
            raise loopingcall.LoopingCallDone()

        expiration = timeutils.utcnow() + datetime.timedelta(
                             seconds=CONF.zvm_reachable_timeout)
        LOG.info(_("Wait neturon-zvm-agent to add NICs to %s user direct."),
                 inst_name, instance=instance)
        timer = loopingcall.FixedIntervalLoopingCall(
                    _wait_for_nics_add_in_vm, inst_name, expiration)
        timer.start(interval=10).wait()

    def _nic_coupled(self, user_direct, vdev, vswitch):
        if vswitch is None:
            return False
        direct_info = user_direct['user_direct']
        nic_str = ("NICDEF %s TYPE QDIO LAN SYSTEM %s" %
                                (vdev.upper(), vswitch.upper()))
        for info in direct_info:
            if nic_str in info:
                return True
        return False

    def _get_host(self):
        return ''.join([pwd.getpwuid(os.geteuid()).pw_name, '@', CONF.my_ip])

    def _import_spawn_image(self, context, image_href, image_os_version):
        LOG.debug("Downloading the image %s from glance to nova compute "
                  "server", image_href)
        image_path = os.path.join(os.path.normpath(CONF.zvm_image_tmp_path),
                                  image_href)
        if not os.path.exists(image_path):
            images.fetch(context, image_href, image_path)
        image_url = "file://" + image_path
        image_meta = {'os_version': image_os_version}
        remote_host = self._get_host()
        self._reqh.call('image_import', image_href, image_url,
                        image_meta, remote_host=remote_host)

    def destroy(self, context, instance, network_info=None,
                block_device_info=None, destroy_disks=False):
        inst_name = instance['name']
        if self._instance_exists(inst_name):
            LOG.info(_("Destroying instance %s"), inst_name,
                     instance=instance)
            self._reqh.call('guest_delete', inst_name)
        else:
            LOG.warning(_('Instance %s does not exist'), inst_name,
                        instance=instance)

    def get_host_uptime(self):
        res = self._reqh.call('host_get_info')
        return res['ipl_time']

    def _instance_power_action(self, instance, action, *args, **kwargs):
        inst_name = instance['name']
        if self._instance_exists(inst_name):
            LOG.info(_("Power action %(action)s to instance %(inst_name)s") %
                     {'action': action, 'inst_name': inst_name},
                     instance=instance)
            self._reqh.call(action, inst_name)
        else:
            LOG.warning(_('Instance %s does not exist'), inst_name,
                        instance=instance)

    def power_off(self, instance, timeout=0, retry_interval=0):
        """Power off the specified instance."""
        if timeout >= 0 and retry_interval > 0:
            self._instance_power_action(instance, 'guest_softstop',
                                        timeout=timeout,
                                        poll_interval=retry_interval)
        else:
            self._instance_power_action(instance, 'guest_softstop')

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        """Power on the specified instance."""
        self._instance_power_action(instance, 'guest_start')

    def pause(self, instance):
        """Pause the z/VM instance."""
        self._instance_power_action(instance, 'guest_pause')

    def unpause(self, instance):
        """Unpause the z/VM instance."""
        self._instance_power_action(instance, 'guest_unpause')

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        if reboot_type == const.REBOOT_TYPE_SOFT:
            self._instance_power_action(instance, 'guest_reboot')
        else:
            self._instance_power_action(instance, 'guest_reset')

    def get_console_output(self, context, instance):
        return self._reqh.call('guest_get_console_output', instance.name)

    def snapshot(self, context, instance, image_id, update_task_state):
        """Create snapshot from a running VM instance.

        :param context: security context
        :param instance: nova.objects.instance.Instance
        :param image_id: Reference to a pre-created image that will
                         hold the snapshot.
        :param update_task_state: Callback function to update the task_state
            on the instance while the snapshot operation progresses. The
            function takes a task_state argument and an optional
            expected_task_state kwarg which defaults to
            nova.compute.task_states.IMAGE_SNAPSHOT. See
            nova.objects.instance.Instance.save for expected_task_state usage.
        """

        # Check the image status
        (image_service, image_id) = glance.get_remote_image_service(
                                                    context, image_id)

        # Update the instance task_state to image_pending_upload
        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)

        # Call zvmsdk guest_capture to generate the image
        try:
            self._reqh.call('guest_capture', instance['name'], image_id)
        except Exception as err:
            with excutils.save_and_reraise_exception():
                LOG.error(_("Failed to capture the instance %(instance)s "
                            "to generate an image with reason: %(err)s"),
                          {'instance': instance['name'], 'err': err},
                           instance=instance)
                # Clean up the image from glance
                image_service.delete(context, image_id)

        # Export the image to nova-compute server temporary
        image_path = os.path.join(os.path.normpath(
                            CONF.zvm_image_tmp_path), image_id)
        dest_path = "file://" + image_path
        try:
            resp = self._reqh.call('image_export', image_id,
                                   dest_path,
                                   remote_host=self._get_host())
        except Exception as err:
            LOG.error(_("Failed to export image %s from SDK server to nova "
                        "compute server") % image_id)
            self._reqh.call('image_delete', image_id)
        # Save image to glance
        new_image_meta = {
            'is_public': False,
            'status': 'active',
            'properties': {
                 'image_location': 'snapshot',
                 'image_state': 'available',
                 'owner_id': instance['project_id'],
                 'os_distro': resp['os_version'],
                 'architecture': const.ARCHITECTURE,
                 'hypervisor_type': const.HYPERVISOR_TYPE
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
            self._pathutils.clean_up_file(image_path)
            self._reqh.call('image_delete', image_id)
        LOG.debug("Snapshot image upload complete", instance=instance)

    def get_volume_connector(self, instance):
        """Get connector information for the instance for attaching to volumes.

        Connector information is a dictionary representing the ip of the
        machine that will be making the connection, the name of the iscsi
        initiator and the hostname of the machine as follows::

            {
                'zvm_fcp': fcp
                'wwpns': [wwpn]
                'host': host
            }
        """
        LOG.debug("Getting volume connector...")
        if not instance:
            errmsg = _("Instance must be provided.")
            raise exception.NovaException(message=errmsg)

        empty_connector = {'zvm_fcp': [], 'wwpns': [], 'host': ''}
        try:
            connector = self._reqh.call('get_volume_connector',
                                        instance['name'])
        except Exception:
            errmsg = _("Get volume connector error.")
            LOG.warning(errmsg)
            return empty_connector

        return connector

    def _format_mountpoint(self, mountpoint):
        """Change mountpoint from /dev/sdX to /dev/vdX.

        When a SCSI device is pluged in, the system will create a file node
        /dev/sdX for the SCSI device. If the file node exists already as a
        link to another file, the link will be overlayed and the file node
        will be seized by the SCSI device.

        For example, if the first SCSI device is pluged in and the mountpoint
        is specified as /dev/sdb, the SCSI device will be attached to /dev/sda
        and /dev/sdb is created as a link to /dev/sda. Then the second SCSI
        device is pluged in. It will be attached to /dev/sdb and the link will
        no longer exist.

        To avoid this case, if mountpoint is /dev/sdX, it will be changed to
        /dev/vdX. Otherwize it will keep as it is.

        When instance's root_device_name is /dev/dasdX, the mountpoint will be
        changed to /dev/dX. That's not what is expected. Format mountpoint to
        /dev/vdX in this case.

        :param mountpoint: The file node name of the mountpoint.

        """

        mountpoint = mountpoint.lower()
        mountpoint = mountpoint.replace('/dev/d', '/dev/sd')
        return mountpoint.replace('/dev/s', '/dev/v')

    def _get_pair_of_wwpn_lun(self, wwpn, lun):
        """Find a suitable pair of WWPN and LUN in device pool based
        on requested size.
        """
        target_lun = "%04x000000000000" % int(lun)
        target_lun = '0x' + target_lun
        # TODO(How to choose a WWPN? Use the first one for now)
        target_wwpn = wwpn[0]
        target_wwpn = '0x' + target_wwpn
        return (target_wwpn, target_lun)

    def _build_connection_info(self, context, connection_info, instance,
                               mountpoint):
        wwpn = connection_info['data']['target_wwn']
        if not wwpn:
            # TODO(exception)
            pass
        lun = connection_info['data']['target_lun']
        fcp = connection_info['data']['zvm_fcp']
        if not fcp:
            # TODO(exception)
            pass
        zvm_fcp = fcp[0]

        (target_wwpn, target_lun) = self._get_pair_of_wwpn_lun(wwpn, lun)

        conn_info = {}
        conn_info['target_wwpn'] = target_wwpn
        conn_info['target_lun'] = target_lun
        conn_info['mount_point'] = mountpoint
        conn_info['assigner_id'] = instance['name']
        conn_info['multipath'] = True
        conn_info['os_version'] = instance.system_metadata['image_os_version']
        conn_info['zvm_fcp'] = zvm_fcp
        return conn_info

    def _attach_volume_to_instance(self, context, connection_info, instance,
                                  mountpoint):
        conn_info = self._build_connection_info(context, connection_info,
                                                instance, mountpoint)
        res = self._reqh.call('volume_attach', conn_info)
        return res

    def _detach_volume_from_instance(self, context, connection_info, instance,
                                    mountpoint):
        conn_info = self._build_connection_info(context, connection_info,
                                                instance, mountpoint)
        res = self._reqh.call('volume_detach', conn_info)
        return res

    def attach_volume(self, context, connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None):
        instance_info = self.get_info(instance)
        if instance_info.state == power_state.PAUSED:
            msg = _("Attaching to a paused instance is not supported.")
            raise exception.NovaException(message=msg)
        if mountpoint:
            mountpoint = self._format_mountpoint(mountpoint)
        if self.instance_exists(instance):
            self._attach_volume_to_instance(context, connection_info, instance,
                                            mountpoint)

    def detach_volume(self, context, connection_info, instance, mountpoint,
                      encryption=None):
        instance_info = self.get_info(instance)
        if instance_info.state == power_state.PAUSED:
            msg = _("Attaching to a paused instance is not supported.")
            raise exception.NovaException(message=msg)
        if mountpoint:
            mountpoint = self._format_mountpoint(mountpoint)
        if self.instance_exists(instance):
            self._detach_volume_from_instance(context, connection_info,
                                              instance, mountpoint)
