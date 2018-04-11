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

from oslo_log import log as logging
from oslo_serialization import jsonutils

from nova import conf
from nova import exception
from nova.i18n import _
from nova.objects import fields as obj_fields
from nova.virt import driver
from nova_zvm.virt.zvm import guest
from nova_zvm.virt.zvm import hypervisor


LOG = logging.getLogger(__name__)
CONF = conf.CONF


class ZVMDriver(driver.ComputeDriver):
    """z/VM implementation of ComputeDriver."""

    def __init__(self, virtapi):
        super(ZVMDriver, self).__init__(virtapi)

        self._virtapi = virtapi
        self._validate_options()

        self._hypervisor = hypervisor.Hypervisor(
            CONF.zvm.cloud_connector_url, ca_file=CONF.zvm.ca_file)

        LOG.info("The zVM compute driver has been initialized.")

    def _validate_options(self):
        if not CONF.zvm.cloud_connector_url:
            error = _('Must specify cloud_connector_url in zvm config '
                      'group to use compute_driver=zvm.driver.ZVMDriver')
            raise exception.NovaException(error=error)

        # Try a test to ensure length of give guest is smaller than 8
        _test_instance = CONF.instance_name_template % 0
        # For zVM instance, limit the maximum length of instance name to 8
        if len(_test_instance) > 8:
            msg = _("Can't spawn instance with template '%s', "
                    "The zVM hypervisor does not support instance names "
                    "longer than 8 characters. Please change your config of "
                    "instance_name_template.") % CONF.instance_name_template
            raise exception.NovaException(error=msg)

    def init_host(self, host):
        pass

    def list_instances(self):
        return self._hypervisor.list_names()

    def get_available_resource(self, nodename=None):
        host_stats = self._hypervisor.get_available_resource()

        hypervisor_hostname = self._hypervisor.get_available_nodes()[0]
        res = {
            'vcpus': host_stats.get('vcpus', 0),
            'memory_mb': host_stats.get('memory_mb', 0),
            'local_gb': host_stats.get('disk_total', 0),
            'vcpus_used': host_stats.get('vcpus_used', 0),
            'memory_mb_used': host_stats.get('memory_mb_used', 0),
            'local_gb_used': host_stats.get('disk_used', 0),
            'hypervisor_type': host_stats.get('hypervisor_type',
                                              obj_fields.HVType.ZVM),
            'hypervisor_version': host_stats.get('hypervisor_version', ''),
            'hypervisor_hostname': host_stats.get('hypervisor_hostname',
                                                  hypervisor_hostname),
            'cpu_info': jsonutils.dumps(host_stats.get('cpu_info', {})),
            'disk_available_least': host_stats.get('disk_available', 0),
            'supported_instances': [(obj_fields.Architecture.S390X,
                                     obj_fields.HVType.ZVM,
                                     obj_fields.VMMode.HVM)],
            'numa_topology': None,
        }

        LOG.debug("Getting available resource for %(host)s:%(nodename)s",
                  {'host': CONF.host, 'nodename': nodename})

        return res

    def get_available_nodes(self, refresh=False):
        return self._hypervisor.get_available_nodes(refresh=refresh)

    def get_info(self, instance):
        _guest = guest.Guest(self._hypervisor, instance)
        return _guest.get_info()

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, allocations, network_info=None,
              block_device_info=None, flavor=None):
        _guest = guest.Guest(self._hypervisor, instance, virtapi=self.virtapi)

        _guest.spawn(context, image_meta, injected_files,
                     admin_password, allocations,
                     network_info=network_info,
                     block_device_info=block_device_info, flavor=flavor)

    def destroy(self, context, instance, network_info=None,
                block_device_info=None, destroy_disks=False):
        _guest = guest.Guest(self._hypervisor, instance)
        _guest.destroy(context, network_info=network_info,
                       block_device_info=block_device_info,
                       destroy_disks=destroy_disks)

    def get_host_uptime(self):
        return self._hypervisor.get_host_uptime()

    def snapshot(self, context, instance, image_id, update_task_state):
        _guest = guest.Guest(self._hypervisor, instance)
        _guest.snapshot(context, image_id, update_task_state)

    def _guest_power_action(self, instance, action, *args, **kwargs):
        if self.instance_exists(instance):
            LOG.info("Power action %(action)s to instance", {'action': action},
                     instance=instance)
            _guest = guest.Guest(self._hypervisor, instance)
            _guest.guest_power_action(action, *args, **kwargs)
        else:
            LOG.warning("Instance does not exist", instance=instance)

    def power_off(self, instance, timeout=0, retry_interval=0):
        if timeout >= 0 and retry_interval > 0:
            self._guest_power_action(instance, 'guest_softstop',
                                     timeout=timeout,
                                     poll_interval=retry_interval)
        else:
            self._guest_power_action(instance, 'guest_softstop')

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        self._guest_power_action(instance, 'guest_start')

    def pause(self, instance):
        self._guest_power_action(instance, 'guest_pause')

    def unpause(self, instance):
        self._guest_power_action(instance, 'guest_unpause')

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        if reboot_type == 'SOFT':
            self._guest_power_action(instance, 'guest_reboot')
        else:
            self._guest_power_action(instance, 'guest_reset')

    def get_console_output(self, context, instance):
        return self._hypervisor.guest_get_console_output(instance.name)
