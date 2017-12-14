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

import copy
import eventlet
import mock

from nova.compute import power_state
from nova import context
from nova import exception
from nova.network import model as network_model
from nova import objects
from nova import test
from nova.tests.unit import fake_instance
from nova.tests import uuidsentinel

from nova_zvm.virt.zvm import driver as zvmdriver
from nova_zvm.virt.zvm import utils as zvmutils


class TestZVMDriver(test.NoDBTestCase):

    def setUp(self):
        super(TestZVMDriver, self).setUp()
        self.flags(zvm_cloud_connector_url='https://1.1.1.1:1111',
                   zvm_image_tmp_path='/test/image',
                   zvm_reachable_timeout=300)
        self.flags(my_ip='192.168.1.1',
                   instance_name_template='test%04x')
        with mock.patch('nova_zvm.virt.zvm.utils.'
                        'zVMConnectorRequestHandler.call') as mcall:
            mcall.return_value = {'hypervisor_hostname': 'TESTHOST',
                                  'ipl_time': 'TESTTIME'}
            self.driver = zvmdriver.ZVMDriver('virtapi')
        self._context = context.RequestContext('fake_user', 'fake_project')
        self._uuid = uuidsentinel.foo
        self._image_id = uuidsentinel.foo
        self._instance_values = {
            'display_name': 'test',
            'uuid': self._uuid,
            'vcpus': 1,
            'memory_mb': 1024,
            'image_ref': self._image_id,
            'root_gb': 0,
        }
        self._instance = fake_instance.fake_instance_obj(
                                self._context, **self._instance_values)
        self._flavor = objects.Flavor(name='testflavor', memory_mb=512,
                                      vcpus=1, root_gb=3, ephemeral_gb=10,
                                      swap=0, extra_specs={})
        self._instance.flavor = self._flavor

        self._eph_disks = [{'guest_format': u'ext3',
                      'device_name': u'/dev/sdb',
                      'disk_bus': None,
                      'device_type': None,
                      'size': 1},
                     {'guest_format': u'ext4',
                      'device_name': u'/dev/sdc',
                      'disk_bus': None,
                      'device_type': None,
                      'size': 2}]
        self._block_device_info = {'swap': None,
                                   'root_device_name': u'/dev/sda',
                                   'ephemerals': self._eph_disks,
                                   'block_device_mapping': []}
        fake_image_meta = {'status': 'active',
                                 'properties': {'os_distro': 'rhel7.2'},
                                 'name': 'rhel72eckdimage',
                                 'deleted': False,
                                 'container_format': 'bare',
                                 'disk_format': 'raw',
                                 'id': self._image_id,
                                 'owner': 'cfc26f9d6af948018621ab00a1675310',
                                 'checksum': 'b026cd083ef8e9610a29eaf71459cc',
                                 'min_disk': 0,
                                 'is_public': False,
                                 'deleted_at': None,
                                 'min_ram': 0,
                                 'size': 465448142}
        self._image_meta = objects.ImageMeta.from_dict(fake_image_meta)
        subnet_4 = network_model.Subnet(cidr='192.168.0.1/24',
                                        dns=[network_model.IP('192.168.0.1')],
                                        gateway=
                                            network_model.IP('192.168.0.1'),
                                        ips=[
                                            network_model.IP('192.168.0.100')],
                                        routes=None)
        network = network_model.Network(id=0,
                                        bridge='fa0',
                                        label='fake',
                                        subnets=[subnet_4],
                                        vlan=None,
                                        bridge_interface=None,
                                        injected=True)
        self._network_values = {
            'id': None,
            'address': 'DE:AD:BE:EF:00:00',
            'network': network,
            'type': network_model.VIF_TYPE_OVS,
            'devname': None,
            'ovs_interfaceid': None,
            'rxtx_cap': 3
        }
        self._network_info = network_model.NetworkInfo([
                network_model.VIF(**self._network_values)
        ])

    def test_driver_init(self):
        self.assertEqual(self.driver._hypervisor_hostname, 'TESTHOST')
        self.assertIsInstance(self.driver._reqh,
                              zvmutils.zVMConnectorRequestHandler)
        self.assertIsInstance(self.driver._vmutils, zvmutils.VMUtils)
        self.assertIsInstance(self.driver._imageop_semaphore,
                              eventlet.semaphore.Semaphore)

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

    def test_private_mapping_power_stat(self):
        status = self.driver._mapping_power_stat('on')
        self.assertEqual(power_state.RUNNING, status)
        status = self.driver._mapping_power_stat('off')
        self.assertEqual(power_state.SHUTDOWN, status)
        status = self.driver._mapping_power_stat('bad')
        self.assertEqual(power_state.NOSTATE, status)

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_get_info_err_InstanceNotFound(self, call):
        call.side_effect = exception.NovaException(results={'overallRC': 404})
        self.assertRaises(exception.InstanceNotFound, self.driver.get_info,
                          self._instance)

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_get_info_err_general(self, call):
        call.side_effect = exception.NovaException(results={'overallRC': 500})
        self.assertRaises(exception.NovaException, self.driver.get_info,
                          self._instance)

    @mock.patch('nova.virt.hardware.InstanceInfo')
    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_get_info(self, call, InstanceInfo):
        call.return_value = 'on'
        self.driver.get_info(self._instance)
        call.assert_called_once_with('guest_get_power_state',
                                     self._instance['name'])
        InstanceInfo.assert_called_once_with(power_state.RUNNING)

    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver.list_instances')
    def test_private_instance_exists_True(self, list_instances):
        list_instances.return_value = ['vm1', 'vm2']
        res = self.driver._instance_exists('vm1')
        self.assertTrue(res)

    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver.list_instances')
    def test_private_instance_exists_False(self, list_instances):
        list_instances.return_value = ['vm1', 'vm2']
        res = self.driver._instance_exists('vm3')
        self.assertFalse(res)

    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._instance_exists')
    def test_instance_exists(self, is_exists):
        is_exists_response = []
        is_exists_response.append(True)
        is_exists_response.append(False)
        is_exists.side_effect = is_exists_response
        res = self.driver.instance_exists(self._instance)
        is_exists.assert_any_call(self._instance.name)
        self.assertTrue(res)

        res = self.driver.instance_exists(self._instance)
        is_exists.assert_any_call(self._instance.name)
        self.assertFalse(res)

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_private_get_image_info_err(self, call):
        call.side_effect = exception.NovaException(results={'overallRC': 500})
        self.assertRaises(exception.NovaException, self.driver._get_image_info,
                          'context', 'image_meta_id', 'os_distro')

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._import_spawn_image')
    def test_private_get_image_info(self, image_import, call):
        call_response = []
        call_response.append(exception.NovaException(results=
                                                     {'overallRC': 404}))
        call_response.append('Query_Result')
        call.side_effect = call_response
        self.driver._get_image_info('context', 'image_meta_id', 'os_distro')
        call.assert_any_call('image_query', imagename='image_meta_id')
        image_import.assert_called_once_with('context', 'image_meta_id',
                                             'os_distro')
        call.assert_any_call('image_query', imagename='image_meta_id')

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_private_get_image_info_exist(self, call):
        call.return_value = 'image-info'
        res = self.driver._get_image_info('context', 'image_meta_id',
                                          'os_distro')
        call.assert_any_call('image_query', imagename='image_meta_id')
        self.assertEqual('image-info', res)

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def _test_set_disk_list(self, call, has_get_root_units=False,
                            has_eph_disks=False):
        disk_list = [{'is_boot_disk': True, 'size': '3g'}]
        eph_disk_list = [{'format': u'ext3', 'size': '1g'},
                         {'format': u'ext4', 'size': '2g'}]
        _inst = copy.copy(self._instance)
        _bdi = copy.copy(self._block_device_info)

        if has_get_root_units:
            # overwrite
            disk_list = [{'is_boot_disk': True, 'size': '3338'}]
            call.return_value = '3338'
            _inst['root_gb'] = 0
        else:
            _inst['root_gb'] = 3

        if has_eph_disks:
            disk_list += eph_disk_list
        else:
            _bdi['ephemerals'] = []
            eph_disk_list = []

        res1, res2 = self.driver._set_disk_list(_inst, self._image_meta.id,
                                                _bdi)

        if has_get_root_units:
            call.assert_called_once_with('image_get_root_disk_size',
                                         self._image_meta.id)
        self.assertEqual(disk_list, res1)
        self.assertEqual(eph_disk_list, res2)

    def test_private_set_disk_list_simple(self):
        self._test_set_disk_list()

    def test_private_set_disk_list_with_eph_disks(self):
        self._test_set_disk_list(has_eph_disks=True)

    def test_private_set_disk_list_with_get_root_units(self):
        self._test_set_disk_list(has_get_root_units=True)

    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_private_setup_network(self, call):
        inst_nets = []
        _net = {'ip_addr': '192.168.0.100',
                    'gateway_addr': '192.168.0.1',
                    'cidr': '192.168.0.1/24',
                    'mac_addr': 'DE:AD:BE:EF:00:00',
                    'nic_id': None}
        inst_nets.append(_net)
        self.driver._setup_network('vm_name', 'os_distro', self._network_info,
                                   self._instance)
        call.assert_any_call('guest_create_network_interface',
                             'vm_name', 'os_distro', inst_nets)

    def test_private_nic_coupled(self):
        user_direct = {'user_direct':
                            ['User TEST',
                             "NICDEF 1000 TYPE QDIO LAN SYSTEM TESTVS"]}
        res = self.driver._nic_coupled(user_direct, '1000', 'TESTVS')
        self.assertTrue(res)

        res = self.driver._nic_coupled(user_direct, '2000', 'TESTVS')
        self.assertFalse(res)

        res = self.driver._nic_coupled(user_direct, '1000', None)
        self.assertFalse(res)

    @mock.patch('pwd.getpwuid')
    def test_private_get_host(self, getpwuid):
        class FakePwuid(object):
            def __init__(self):
                self.pw_name = 'test'
        getpwuid.return_value = FakePwuid()
        res = self.driver._get_host()
        self.assertEqual('test@192.168.1.1', res)

    @mock.patch('nova.virt.images.fetch')
    @mock.patch('os.path.exists')
    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._get_host')
    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_private_import_spawn_image(self, call, get_host, exists, fetch):
        get_host.return_value = 'test@192.168.1.1'
        exists.return_value = False

        image_url = "file:///test/image/image_name"
        image_meta = {'os_version': 'os_version'}
        self.driver._import_spawn_image(self._context, 'image_name',
                                        'os_version')
        fetch.assert_called_once_with(self._context, 'image_name',
                                      "/test/image/image_name")
        get_host.assert_called_once_with()
        call.assert_called_once_with('image_import', 'image_name', image_url,
                        image_meta, remote_host='test@192.168.1.1')

    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._instance_exists')
    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_destroy(self, call, instance_exists):
        instance_exists.return_value = True
        self.driver.destroy(self._context, self._instance,
                            network_info=self._network_info)
        call.assert_called_once_with('guest_delete', self._instance['name'])

    def test_get_host_uptime(self):
        with mock.patch('nova_zvm.virt.zvm.utils.'
                        'zVMConnectorRequestHandler.call') as mcall:
            mcall.return_value = {'hypervisor_hostname': 'TESTHOST',
                                  'ipl_time': 'TESTTIME'}
            time = self.driver.get_host_uptime()
            self.assertEqual('TESTTIME', time)

    def test_spawn_invalid_userid(self):
        self.flags(instance_name_template='test%05x')
        self.addCleanup(self.flags, instance_name_template='test%04x')
        invalid_inst = fake_instance.fake_instance_obj(self._context,
                                                       name='123456789')
        self.assertRaises(exception.InvalidInput, self.driver.spawn,
                          self._context, invalid_inst, self._image_meta,
                          injected_files=None, admin_password=None,
                          allocations=None, network_info=self._network_info,
                          block_device_info=self._block_device_info,
                          flavor=self._flavor)

    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._wait_network_ready')
    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._setup_network')
    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._get_host')
    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._set_disk_list')
    @mock.patch.object(zvmutils.VMUtils, 'generate_configdrive')
    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._get_image_info')
    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_spawn(self, call, get_image_info, gen_conf_file, set_disk_list,
                    get_host, setup_network, wait_ready):
        _inst = copy.copy(self._instance)
        _bdi = copy.copy(self._block_device_info)
        get_image_info.return_value = [['image_name']]
        gen_conf_file.return_value = 'transportfiles'
        set_disk_list.return_value = 'disk_list', 'eph_list'
        get_host.return_value = 'test@192.168.1.1'
        setup_network.return_value = ''
        wait_ready.return_value = ''
        call_resp = ['', '', '', '']
        call.side_effect = call_resp

        self.driver.spawn(self._context, _inst, self._image_meta,
                          injected_files=None, admin_password=None,
                          allocations=None, network_info=self._network_info,
                          block_device_info=_bdi, flavor=self._flavor)
        gen_conf_file.assert_called_once_with(self._context, _inst,
                                              None, None)
        get_image_info.assert_called_once_with(self._context,
                                    self._image_meta.id,
                                    self._image_meta.properties.os_distro)
        set_disk_list.assert_called_once_with(_inst, 'image_name', _bdi)
        call.assert_any_call('guest_create', _inst['name'],
                             1, 1024, disk_list='disk_list')
        get_host.assert_called_once_with()
        call.assert_any_call('guest_deploy', _inst['name'], 'image_name',
                             transportfiles='transportfiles',
                             remotehost='test@192.168.1.1')
        setup_network.assert_called_once_with(_inst['name'],
                                self._image_meta.properties.os_distro,
                                self._network_info, _inst)
        call.assert_any_call('guest_config_minidisks', _inst['name'],
                             'eph_list')
        wait_ready.assert_called_once_with(_inst)
        call.assert_any_call('guest_start', _inst['name'])

    @mock.patch('nova_zvm.virt.zvm.driver.ZVMDriver._nic_coupled')
    @mock.patch('nova_zvm.virt.zvm.utils.zVMConnectorRequestHandler.call')
    def test_private_wait_network_ready(self, call, nic_coupled):
        call_resp = []
        switch_dict = {'1000': 'TESTVM'}
        user_direct = {'user_direct':
                            ['User TEST',
                             "NICDEF 1000 TYPE QDIO LAN SYSTEM TESTVS"]}
        call_resp.append(switch_dict)
        call_resp.append(user_direct)
        call.side_effect = call_resp
        nic_coupled.return_value = True
        self.driver._wait_network_ready(self._instance)
        call.assert_any_call('guest_get_nic_vswitch_info',
                             self._instance['name'])

        call.assert_any_call('guest_get_definition_info',
                             self._instance['name'])
