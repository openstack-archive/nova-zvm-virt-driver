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

import mock

from nova import context
from nova import exception
from nova import test
from nova.tests.unit import fake_instance
from nova.tests import uuidsentinel
from nova_zvm.virt.zvm import driver as zvmdriver


class TestZVMDriver(test.NoDBTestCase):

    def setUp(self):
        super(TestZVMDriver, self).setUp()
        self.flags(instance_name_template='abc%05d')
        self.flags(cloud_connector_url='https://1.1.1.1:1111', group='zvm')
        with mock.patch('nova_zvm.virt.zvm.utils.'
                        'ConnectorClient.call') as mcall:
            mcall.return_value = {'hypervisor_hostname': 'TESTHOST',
                                  'ipl_time': 'IPL at 11/14/17 10:47:44 EST'}
            self._driver = zvmdriver.ZVMDriver('virtapi')
            self._hypervisor = self._driver._hypervisor

        self._context = context.RequestContext('fake_user', 'fake_project')
        self._instance = fake_instance.fake_instance_obj(self._context)

    def test_driver_init_no_url(self):
        self.flags(cloud_connector_url=None, group='zvm')
        self.assertRaises(exception.NovaException,
                          zvmdriver.ZVMDriver, 'virtapi')

    @mock.patch('nova_zvm.virt.zvm.utils.ConnectorClient.call')
    def test_get_available_resource_err_case(self, call):
        call.side_effect = exception.NovaException(error='dummy')
        results = self._driver.get_available_resource()
        self.assertEqual(0, results['vcpus'])
        self.assertEqual(0, results['memory_mb_used'])
        self.assertEqual(0, results['disk_available_least'])
        self.assertEqual('TESTHOST', results['hypervisor_hostname'])

    def test_driver_template_validation(self):
        self.flags(instance_name_template='abc%6d')
        self.assertRaises(exception.NovaException,
                          self._driver._validate_options)

    @mock.patch('nova_zvm.virt.zvm.guest.Guest.get_info')
    def test_get_info(self, mock_get):
        self._driver.get_info(self._instance)
        mock_get.assert_called_once_with()

    @mock.patch('nova_zvm.virt.zvm.guest.Guest.spawn')
    def test_spawn(self, mock_spawn):
        image_meta = {}
        injected_files = {}
        admin_password = 'dummy'
        allocations = {}
        self._driver.spawn(self._context, self._instance, image_meta,
                          injected_files, admin_password, allocations)
        mock_spawn.assert_called_once_with(self._context,
            image_meta, injected_files, admin_password, allocations,
            network_info=None, block_device_info=None, flavor=None)

    @mock.patch('nova_zvm.virt.zvm.guest.Guest.destroy')
    def test_destroy(self, mock_spawn):
        self._driver.destroy(self._context, self._instance)
        mock_spawn.assert_called_once_with(self._context,
            network_info=None, block_device_info=None, destroy_disks=False)

    @mock.patch('nova_zvm.virt.zvm.guest.Guest.snapshot')
    def test_snapshot(self, mock_snapshot):
        def fake_update_task_state():
            pass

        image_id = uuidsentinel.image_id
        self._driver.snapshot(self._context, self._instance, image_id,
                              fake_update_task_state)
        mock_snapshot.assert_called_once_with(self._context, image_id,
                                              fake_update_task_state)

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_guest_power_action(self, call, mock_exists):
        mock_exists.return_value = True
        self._driver._guest_power_action(self._instance, 'guest_start')
        call.assert_called_once_with('guest_start')

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.utils.ConnectorClient.call')
    def test_guest_power_action_not_exist(self, call, mock_exists):
        mock_exists.return_value = False
        self._driver._guest_power_action(self._instance, 'guest_start')
        self.assertEqual(0, call.called)

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_power_off(self, ipa, mock_exists):
        mock_exists.return_value = True
        self._driver.power_off(self._instance)
        ipa.assert_called_once_with('guest_softstop')

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_power_off_with_timeout_interval(self, ipa, mock_exists):
        mock_exists.return_value = True
        self._driver.power_off(self._instance, 60, 10)
        ipa.assert_called_once_with('guest_softstop',
                                    timeout=60, poll_interval=10)

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_power_on(self, ipa, mock_exists):
        mock_exists.return_value = True
        self._driver.power_on(None, self._instance, None)
        ipa.assert_called_once_with('guest_start')

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_pause(self, ipa, mock_exists):
        mock_exists.return_value = True
        self._driver.pause(self._instance)
        ipa.assert_called_once_with('guest_pause')

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_unpause(self, ipa, mock_exists):
        mock_exists.return_value = True
        self._driver.unpause(self._instance)
        ipa.assert_called_once_with('guest_unpause')

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_reboot_soft(self, ipa, mock_exists):
        mock_exists.return_value = True
        self._driver.reboot(None, self._instance, None, 'SOFT')
        ipa.assert_called_once_with('guest_reboot')

    @mock.patch('nova.virt.driver.ComputeDriver.instance_exists')
    @mock.patch('nova_zvm.virt.zvm.guest.Guest.guest_power_action')
    def test_reboot_hard(self, ipa, mock_exists):
        mock_exists.return_value = True
        self._driver.reboot(None, self._instance, None, 'HARD')
        ipa.assert_called_once_with('guest_reset')

    @mock.patch('nova_zvm.virt.zvm.utils.ConnectorClient.call')
    def test_get_console_output(self, call):
        call.return_value = 'console output'
        outputs = self._driver.get_console_output(None, self._instance)
        call.assert_called_once_with('guest_get_console_output', 'abc00001')
        self.assertEqual('console output', outputs)
