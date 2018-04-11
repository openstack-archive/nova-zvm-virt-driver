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

import os
import pwd

from oslo_log import log as logging

from nova.compute import power_state as compute_power_state
from nova import conf
from nova import exception
from nova.virt.zvm import utils as zvmutils


LOG = logging.getLogger(__name__)
CONF = conf.CONF


class Hypervisor(object):
    """z/VM implementation of Hypervisor."""

    def __init__(self, zcc_url, ca_file=None):
        super(Hypervisor, self).__init__()

        self._reqh = zvmutils.ConnectorClient(zcc_url,
                                              ca_file=ca_file)
        host_info = self._get_host_info()

        self._hypervisor_hostname = host_info['hypervisor_hostname']
        self._ipl_time = host_info['ipl_time']

        self._rhost = ''.join([pwd.getpwuid(os.geteuid()).pw_name, '@',
                               CONF.my_ip])

    def _get_host_info(self):
        host_stats = {}
        try:
            host_stats = self._reqh.call('host_get_info')
        except exception.ZVMDriverException as e:
            LOG.warning("Failed to get host stats: %s", e)

        return host_stats

    def get_available_resource(self):
        return self._get_host_info()

    def get_available_nodes(self, refresh=False):
        # It's not expected that the hostname change, no need to take
        # 'refresh' into account.
        return [self._hypervisor_hostname]

    def list_names(self):
        return self._reqh.call('guest_list')

    def get_host_uptime(self):
        return self._ipl_time

    def guest_exists(self, instance):
        return instance.name in self.list_names()

    def guest_get_power_state(self, name):
        power_state = compute_power_state.NOSTATE
        try:
            power_state = self._reqh.call('guest_get_power_state', name)
        except exception.NovaException as err:
            if err.kwargs['results']['overallRC'] == 404:
                # instance does not exist
                LOG.warning("Get power state of nonexistent instance: %s",
                            name)
                raise exception.InstanceNotFound(instance_id=name)
            else:
                raise

        return power_state

    def guest_create(self, name, vcpus, memory_mb, disk_list):
        self._reqh.call('guest_create', name, vcpus, memory_mb,
                        disk_list=disk_list)

    def guest_deploy(self, name, image_name, transportfiles):
        self._reqh.call('guest_deploy', name, image_name,
                        transportfiles, self._rhost)

    def guest_delete(self, name):
        self._reqh.call('guest_delete', name)

    def guest_start(self, name):
        self._reqh.call('guest_start', name)

    def guest_create_network_interface(self, name, distro, nets):
        self._reqh.call('guest_create_network_interface',
                        name, distro, nets)

    def guest_get_definition_info(self, name):
        return self._reqh.call('guest_get_definition_info', name)

    def guest_get_nic_vswitch_info(self, name):
        return self._reqh.call('guest_get_nic_vswitch_info', name)

    def guest_config_minidisks(self, name, disk_list):
        self._reqh.call('guest_config_minidisks', name, disk_list)

    def guest_capture(self, name, image_id):
        self._reqh.call('guest_capture', name, image_id)

    def guest_power_action(self, action, name, *args, **kwargs):
        self._reqh.call(action, name, *args, **kwargs)

    def guest_get_console_output(self, name):
        return self._reqh.call('guest_get_console_output', name)

    def image_query(self, imagename):
        return self._reqh.call('image_query', imagename=imagename)

    def image_get_root_disk_size(self, imagename):
        return self._reqh.call('image_get_root_disk_size', imagename)

    def image_import(self, image_href, image_url, image_meta):
        self._reqh.call('image_import', image_href, image_url,
                        image_meta, self._rhost)

    def image_export(self, image_id, dest_path):
        resp = self._reqh.call('image_export', image_id,
                               dest_path, self._rhost)
        return resp

    def image_delete(self, image_id):
        self._reqh.call('image_delete', image_id)
