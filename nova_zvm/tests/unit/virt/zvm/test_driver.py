# Copyright 2017 IBM Corp.
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

from nova import exception
from nova import test

from nova_zvm.virt.zvm import driver as zvmdriver


class TestZVMDriver(test.NoDBTestCase):

    def setUp(self):
        super(TestZVMDriver, self).setUp()
        self.flags(zvm_cloud_connector_url='https://1.1.1.1:1111')
        with mock.patch('nova_zvm.virt.zvm.utils.'
                        'zVMConnectorRequestHandler.call') as mcall:
            mcall.return_value = {'hypervisor_hostname': 'TESTHOST'}
            self.driver = zvmdriver.ZVMDriver('virtapi')

    def test_driver_init(self):
        self.assertEqual(self.driver._hypervisor_hostname, 'TESTHOST')

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_list_instance(self, call):
        call.return_value = ['vm1', 'vm2']
        inst_list = self.driver.list_instances()
        self.assertEqual(['vm1', 'vm2'], inst_list)

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_get_available_resource(self, call):
        host_info = {'disk_available': 1144,
                     'ipl_time': u'IPL at 11/14/17 10:47:44 EST',
                     'vcpus_used': 4,
                     'hypervisor_type': u'zvm',
                     'disk_total': 2000,
                     'zvm_host': u'TESTHOST',
                     'memory_mb': 78192.0,
                     'cpu_info': {u'cec_model': u'2827',
                                  u'architecture': u's390x'},
                     'vcpus': 84,
                     'hypervisor_hostname': u'TESTHOST',
                     'hypervisor_version': 640,
                     'disk_used': 856,
                     'memory_mb_used': 8192.0}
        call.return_value = host_info
        results = self.driver.get_available_resource()
        self.assertEqual(84, results['vcpus'])
        self.assertEqual(8192.0, results['memory_mb_used'])
        self.assertEqual(1144, results['disk_available_least'])
        self.assertEqual('TESTHOST', results['hypervisor_hostname'])

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_get_available_resource_err_case(self, call):
        call.side_effect = exception.NovaException
        results = self.driver.get_available_resource()
        self.assertEqual(0, results['vcpus'])
        self.assertEqual(0, results['memory_mb_used'])
        self.assertEqual(0, results['disk_available_least'])
        self.assertEqual('', results['hypervisor_hostname'])

    def test_get_available_nodes(self):
        nodes = self.driver.get_available_nodes()
        self.assertEqual(['TESTHOST'], nodes)
