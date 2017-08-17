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
import six
import time

from nova.compute import power_state
from nova import exception as nova_exception
from nova.i18n import _
from nova.image import api as image_api
from nova.objects import fields as obj_fields
from nova.virt import driver
from nova.virt import hardware
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_service import loopingcall
from oslo_utils import excutils
from oslo_utils import timeutils
from zvmsdk import api as sdkapi
from zvmsdk import exception as sdkexception

from nova_zvm.virt.zvm import conf
from nova_zvm.virt.zvm import const
from nova_zvm.virt.zvm import exception
from nova_zvm.virt.zvm import utils as zvmutils


LOG = logging.getLogger(__name__)

CONF = conf.CONF
CONF.import_opt('default_ephemeral_format', 'nova.conf')
CONF.import_opt('host', 'nova.conf')
CONF.import_opt('my_ip', 'nova.conf')


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
        self._sdk_api = sdkapi.SDKAPI()
        self._vmutils = zvmutils.VMUtils()

        self._image_api = image_api.API()
        self._pathutils = zvmutils.PathUtils()
        self._imageutils = zvmutils.ImageUtils()
        self._networkutils = zvmutils.NetworkUtils()
        self._imageop_semaphore = eventlet.semaphore.Semaphore(1)

        # incremental sleep interval list
        _inc_slp = [5, 10, 20, 30, 60]
        _slp = 5

        self._host_stats = []
        _slp = 5

        while (self._host_stats == []):
            try:
                self._host_stats = self.update_host_status()
            except Exception as e:
                # Ignore any exceptions and log as warning
                _slp = len(_inc_slp) != 0 and _inc_slp.pop(0) or _slp
                msg = _("Failed to get host stats while initializing zVM "
                        "driver due to reason %(reason)s, will re-try in "
                        "%(slp)d seconds")
                LOG.warning(msg, {'reason': six.text_type(e),
                               'slp': _slp})
                time.sleep(_slp)

    def init_host(self, host):
        """Initialize anything that is necessary for the driver to function,
        including catching up with currently running VM's on the given host.
        """
        pass

    def _get_instance_info(self, instance):
        inst_name = instance['name']
        vm_info = self._sdk_api.guest_get_info(inst_name)
        _instance_info = hardware.InstanceInfo()

        power_stat = zvmutils.mapping_power_stat(vm_info['power_state'])
        if ((power_stat == power_state.RUNNING) and
            (instance['power_state'] == power_state.PAUSED)):
            # return paused state only previous power state is paused
            _instance_info.state = power_state.PAUSED
        else:
            _instance_info.state = power_stat

        _instance_info.max_mem_kb = vm_info['max_mem_kb']
        _instance_info.mem_kb = vm_info['mem_kb']
        _instance_info.num_cpu = vm_info['num_cpu']
        _instance_info.cpu_time_ns = vm_info['cpu_time_us'] * 1000

        return _instance_info

    def get_info(self, instance):
        """Get the current status of an instance, by name (not ID!)

        Returns a dict containing:
        :state:           the running state, one of the power_state codes
        :max_mem:         (int) the maximum memory in KBytes allowed
        :mem:             (int) the memory in KBytes used by the domain
        :num_cpu:         (int) the number of virtual CPUs for the domain
        :cpu_time:        (int) the CPU time used in nanoseconds

        """
        inst_name = instance['name']

        try:
            return self._get_instance_info(instance)
        except sdkexception.ZVMVirtualMachineNotExist:
            LOG.warning(_("z/VM instance %s does not exist") % inst_name,
                        instance=instance)
            raise nova_exception.InstanceNotFound(instance_id=inst_name)
        except Exception as err:
            # TODO(YDY): raise nova_exception.InstanceNotFound
            LOG.warning(_("Failed to get the info of z/VM instance %s") %
                        inst_name, instance=instance)
            raise err

    def list_instances(self):
        """Return the names of all the instances known to the virtualization
        layer, as a list.
        """
        return self._sdk_api.guest_list()

    def _instance_exists(self, instance_name):
        """Overwrite this to using instance name as input parameter."""
        return instance_name in self.list_instances()

    def instance_exists(self, instance):
        """Overwrite this to using instance name as input parameter."""
        return self._instance_exists(instance.name)

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None,
              flavor=None):
        LOG.info(_("Spawning new instance %s on zVM hypervisor") %
                 instance['name'], instance=instance)
        # For zVM instance, limit the maximum length of instance name to \ 8
        if len(instance['name']) > 8:
            msg = (_("Don't support spawn vm on zVM hypervisor with instance "
                "name: %s, please change your instance_name_template to make "
                "sure the length of instance name is not longer than 8 "
                "characters") % instance['name'])
            raise nova_exception.InvalidInput(reason=msg)
        try:
            spawn_start = time.time()
            os_distro = image_meta.properties.os_distro

            # TODO(YaLian) will remove network files from this
            transportfiles = self._vmutils.generate_configdrive(
                            context, instance, os_distro, network_info,
                            injected_files, admin_password)

            with self._imageop_semaphore:
                spawn_image_exist = self._sdk_api.image_query(
                                    image_meta.id)
                if not spawn_image_exist:
                    self._imageutils.import_spawn_image(
                        context, image_meta.id, os_distro)

            spawn_image_name = self._sdk_api.image_query(
                                    image_meta.id)[0]
            if instance['root_gb'] == 0:
                root_disk_size = self._sdk_api.image_get_root_disk_size(
                                                spawn_image_name)
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
            self._sdk_api.guest_create(instance['name'], instance['vcpus'],
                                       instance['memory_mb'], disk_list)

            # Setup network for z/VM instance
            self._setup_network(instance['name'], network_info)
            self._sdk_api.guest_deploy(instance['name'], spawn_image_name,
                                       transportfiles, zvmutils.get_host())

            # Handle ephemeral disks
            if eph_list:
                self._sdk_api.guest_config_minidisks(instance['name'],
                                                     eph_list)

            self._wait_network_ready(instance['name'], instance)

            self._sdk_api.guest_start(instance['name'])
            spawn_time = time.time() - spawn_start
            LOG.info(_("Instance spawned succeeded in %s seconds") %
                     spawn_time, instance=instance)
        except Exception as err:
            with excutils.save_and_reraise_exception():
                LOG.error(_("Deploy image to instance %(instance)s "
                            "failed with reason: %(err)s") %
                          {'instance': instance['name'], 'err': err},
                          instance=instance)
                self.destroy(context, instance, network_info,
                             block_device_info)

    def _setup_network(self, vm_name, network_info):
        LOG.debug("Creating NICs for vm %s", vm_name)
        manage_IP_set = False
        for vif in network_info:
            if not manage_IP_set:
                network = vif['network']
                ip_addr = network['subnets'][0]['ips'][0]['address']
                self._sdk_api.guest_create_nic(vm_name, nic_id=vif['id'],
                                               mac_addr=vif['address'],
                                               ip_addr=ip_addr)
                manage_IP_set = True
            else:
                self._sdk_api.guest_create_nic(vm_name, nic_id=vif['id'],
                                               mac_addr=vif['address'])

    def _wait_network_ready(self, inst_name, instance):
        """Wait until neutron zvm-agent add all NICs to vm"""
        def _wait_for_nics_add_in_vm(inst_name, expiration):
            if (CONF.zvm_reachable_timeout and
                    timeutils.utcnow() > expiration):
                msg = _("NIC update check failed "
                        "on instance:%s") % instance.uuid
                raise exception.ZVMNetworkError(msg=msg)

            try:
                switch_dict = self._sdk_api.guest_get_nic_vswitch_info(
                                                inst_name)
                if switch_dict and '' not in switch_dict.values():
                    for key in switch_dict:
                        result = self._sdk_api.guest_get_definition_info(
                                                inst_name, nic_coupled=key)
                        if not result['nic_coupled']:
                            return
                else:
                    # In this case, the nic switch info is not ready yet
                    # need another loop to check until time out or find it
                    return

            except exception.ZVMBaseException as e:
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

    @property
    def need_legacy_block_device_info(self):
        return False

    def destroy(self, context, instance, network_info=None,
                block_device_info=None, destroy_disks=False):

        inst_name = instance['name']
        if self._instance_exists(inst_name):
            LOG.info(_("Destroying instance %s"), inst_name,
                     instance=instance)
            self._sdk_api.guest_delete(inst_name)
        else:
            LOG.warning(_('Instance %s does not exist'), inst_name,
                        instance=instance)

    def attach_volume(self, context, connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None):
        """Attach the disk to the instance at mountpoint using info."""
        pass

    def detach_volume(self, connection_info, instance, mountpoint=None,
                      encryption=None):
        """Detach the disk attached to the instance."""
        pass

    def power_off(self, instance, timeout=0, retry_interval=0):
        """Power off the specified instance."""
        inst_name = instance['name']
        if self._instance_exists(inst_name):
            LOG.info(_("Powering OFF instance %s"), inst_name,
                     instance=instance)
            self._sdk_api.guest_stop(inst_name, timeout, retry_interval)
        else:
            LOG.warning(_('Instance %s does not exist'), inst_name,
                        instance=instance)

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        """Power on the specified instance."""
        inst_name = instance['name']
        if self._instance_exists(inst_name):
            LOG.info(_("Powering ON instance %s"), inst_name,
                     instance=instance)
            self._sdk_api.guest_start(inst_name)
        else:
            LOG.warning(_('Instance %s does not exist'), inst_name,
                        instance=instance)

    def get_available_resource(self, nodename=None):
        """Retrieve resource information.

        This method is called when nova-compute launches, and
        as part of a periodic task

        :param nodename:
            node which the caller want to get resources from
            a driver that manages only one node can safely ignore this
        :returns: Dictionary describing resources

        """
        LOG.debug("Getting available resource for %s" % CONF.host)
        stats = self.update_host_status()[0]

        mem_used = stats['host_memory_total'] - stats['host_memory_free']
        supported_instances = stats['supported_instances']
        dic = {
            'vcpus': stats['vcpus'],
            'memory_mb': stats['host_memory_total'],
            'local_gb': stats['disk_total'],
            'vcpus_used': stats['vcpus_used'],
            'memory_mb_used': mem_used,
            'local_gb_used': stats['disk_used'],
            'hypervisor_type': stats['hypervisor_type'],
            'hypervisor_version': stats['hypervisor_version'],
            'hypervisor_hostname': stats['hypervisor_hostname'],
            'cpu_info': jsonutils.dumps(stats['cpu_info']),
            'disk_available_least': stats['disk_available'],
            'supported_instances': supported_instances,
            'numa_topology': None,
        }

        return dic

    def ensure_filtering_rules_for_instance(self, instance_ref, network_info):
        # It enforces security groups on host initialization and live
        # migration. In z/VM we do not assume instances running upon host
        # initialization
        return

    def update_host_status(self):
        """Refresh host stats. One compute service entry possibly
        manages several hypervisors, so will return a list of host
        status information.
        """
        LOG.debug("Updating host status for %s" % CONF.host)

        caps = []

        info = self._sdk_api.host_get_info()

        data = {'host': CONF.host,
                'allowed_vm_type': const.ALLOWED_VM_TYPE}
        data['vcpus'] = info['vcpus']
        data['vcpus_used'] = info['vcpus_used']
        data['cpu_info'] = info['cpu_info']
        data['disk_total'] = info['disk_total']
        data['disk_used'] = info['disk_used']
        data['disk_available'] = info['disk_available']
        data['host_memory_total'] = info['memory_mb']
        data['host_memory_free'] = (info['memory_mb'] -
                                    info['memory_mb_used'])
        data['hypervisor_type'] = info['hypervisor_type']
        data['hypervisor_version'] = info['hypervisor_version']
        data['hypervisor_hostname'] = info['hypervisor_hostname']
        data['supported_instances'] = [(const.ARCHITECTURE,
                                        const.HYPERVISOR_TYPE,
                                        obj_fields.VMMode.HVM)]
        data['ipl_time'] = info['ipl_time']

        caps.append(data)

        return caps

    def get_console_output(self, context, instance):
        return self._sdk_api.guest_get_console_output(instance.name)

    def get_host_uptime(self):
        return self._host_stats[0]['ipl_time']

    def get_available_nodes(self, refresh=False):
        return [d['hypervisor_hostname'] for d in self._host_stats
                if (d.get('hypervisor_hostname') is not None)]
