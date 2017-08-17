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


"""Test suite for ZVMDriver."""

import eventlet
import mock

from nova.compute import power_state
from nova import context
from nova import exception as nova_exception
from nova.image import api as image_api
from nova.network import model as network_model
from nova import objects
from nova.objects import fields as obj_fields
from nova import test
from nova.tests.unit import fake_instance
from nova.tests import uuidsentinel
from nova.virt import fake
from nova.virt import hardware
from zvmsdk import api as sdkapi
from zvmsdk import dist
from zvmsdk import exception as sdkexception

from nova_zvm.virt.zvm import conf
from nova_zvm.virt.zvm import const
from nova_zvm.virt.zvm import driver
from nova_zvm.virt.zvm import utils as zvmutils


CONF = conf.CONF
CONF.import_opt('host', 'nova.conf')
CONF.import_opt('my_ip', 'nova.conf')


class ZVMDriverTestCases(test.NoDBTestCase):
    """Unit tests for z/VM driver methods."""

    @mock.patch.object(driver.ZVMDriver, 'update_host_status')
    def setUp(self, update_host_status):
        super(ZVMDriverTestCases, self).setUp()

        self.flags(host='fakehost',
                   my_ip='10.1.1.10',
                   instance_name_template = 'test%04x')
        update_host_status.return_value = [{
            'host': 'fakehost',
            'allowed_vm_type': 'zLinux',
            'vcpus': 10,
            'vcpus_used': 10,
            'cpu_info': {'Architecture': 's390x', 'CEC model': '2097'},
            'disk_total': 406105,
            'disk_used': 367263,
            'disk_available': 38842,
            'host_memory_total': 16384,
            'host_memory_free': 8096,
            'hypervisor_type': 'zvm',
            'hypervisor_version': '630',
            'hypervisor_hostname': 'fakenode',
            'supported_instances': [('s390x', 'zvm', 'hvm')],
            'ipl_time': 'IPL at 03/13/14 21:43:12 EDT',
        }]
        self.driver = driver.ZVMDriver(fake.FakeVirtAPI())
        self._context = context.RequestContext('fake_user', 'fake_project')
        self._uuid = uuidsentinel.foo
        self._image_id = uuidsentinel.foo
        self._instance_values = {
            'display_name': 'test',
            'uuid': self._uuid,
            'vcpus': 1,
            'memory_mb': 1024,
            'image_ref': self._image_id,
            'root_gb': 3,
        }
        self._instance = fake_instance.fake_instance_obj(
                                self._context, **self._instance_values)
        self._flavor = objects.Flavor(name='testflavor', memory_mb=512,
                                      vcpus=1, root_gb=3, ephemeral_gb=10,
                                      swap=0, extra_specs={})
        self._instance.flavor = self._flavor

        eph_disks = [{'guest_format': u'ext3',
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
                                   'ephemerals': eph_disks,
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

    def test_init_driver(self):
        self.assertIsInstance(self.driver._sdk_api, sdkapi.SDKAPI)
        self.assertIsInstance(self.driver._vmutils, zvmutils.VMUtils)
        self.assertIsInstance(self.driver._image_api, image_api.API)
        self.assertIsInstance(self.driver._pathutils, zvmutils.PathUtils)
        self.assertIsInstance(self.driver._imageutils, zvmutils.ImageUtils)
        self.assertIsInstance(self.driver._networkutils,
                              zvmutils.NetworkUtils)
        self.assertIsInstance(self.driver._imageop_semaphore,
                              eventlet.semaphore.Semaphore)
        self.assertEqual(self.driver._host_stats[0]['host'], "fakehost")
        self.assertEqual(self.driver._host_stats[0]['disk_available'], 38842)

    @mock.patch.object(sdkapi.SDKAPI, 'host_get_info')
    def test_update_host_status(self, host_get_info):
        host_get_info.return_value = {
            'vcpus': 10,
            'vcpus_used': 10,
            'cpu_info': {'Architecture': 's390x', 'CEC model': '2097'},
            'disk_total': 406105,
            'disk_used': 367263,
            'disk_available': 38842,
            'memory_mb': 876543,
            'memory_mb_used': 111111,
            'hypervisor_type': 'zvm',
            'hypervisor_version': '630',
            'hypervisor_hostname': 'fakenode',
            'ipl_time': 'IPL at 03/13/14 21:43:12 EDT',
            }
        info = self.driver.update_host_status()
        host_get_info.assert_called_with()
        self.assertEqual(info[0]['host'], CONF.host)
        self.assertEqual(info[0]['hypervisor_hostname'], 'fakenode')
        self.assertEqual(info[0]['host_memory_free'], 765432)

    @mock.patch.object(driver.ZVMDriver, 'update_host_status')
    def test_get_available_resource(self, update_host_status):
        update_host_status.return_value = [{
            'host': CONF.host,
            'allowed_vm_type': const.ALLOWED_VM_TYPE,
            'vcpus': 10,
            'vcpus_used': 10,
            'cpu_info': {'Architecture': 's390x', 'CEC model': '2097'},
            'disk_total': 406105,
            'disk_used': 367263,
            'disk_available': 38842,
            'host_memory_total': 876543,
            'host_memory_free': 111111,
            'hypervisor_type': 'zvm',
            'hypervisor_version': '630',
            'hypervisor_hostname': 'fakenode',
            'supported_instances': [(const.ARCHITECTURE,
                                     const.HYPERVISOR_TYPE,
                                     obj_fields.VMMode.HVM)],
            'ipl_time': 'IPL at 03/13/14 21:43:12 EDT',
            }]
        res = self.driver.get_available_resource('fakenode')
        self.assertEqual(res['vcpus'], 10)
        self.assertEqual(res['memory_mb_used'], 765432)
        self.assertEqual(res['disk_available_least'], 38842)

    @mock.patch.object(sdkapi.SDKAPI, 'guest_list')
    def test_list_instances(self, guest_list):
        self.driver.list_instances()
        guest_list.assert_called_once_with()

    @mock.patch.object(sdkapi.SDKAPI, 'guest_get_info')
    @mock.patch.object(zvmutils, 'mapping_power_stat')
    def test_get_instance_info_paused(self, mapping_power_stat,
                                      guest_get_info):
        guest_get_info.return_value = {'power_state': 'on',
                                    'max_mem_kb': 2097152,
                                    'mem_kb': 44,
                                    'num_cpu': 2,
                                    'cpu_time_us': 796000,
                                    }
        mapping_power_stat.return_value = power_state.RUNNING
        fake_inst = fake_instance.fake_instance_obj(self._context,
                    name='fake', power_state=power_state.PAUSED,
                    memory_mb='1024',
                    vcpus='4')
        inst_info = self.driver._get_instance_info(fake_inst)
        mapping_power_stat.assert_called_once_with('on')
        self.assertEqual(inst_info.state, power_state.PAUSED)
        self.assertEqual(inst_info.mem_kb, 44)

    @mock.patch.object(sdkapi.SDKAPI, 'guest_get_info')
    @mock.patch.object(zvmutils, 'mapping_power_stat')
    def test_get_instance_info_off(self, mapping_power_stat, guest_get_info):
        guest_get_info.return_value = {'power_state': 'off',
                                    'max_mem_kb': 2097152,
                                    'mem_kb': 44,
                                    'num_cpu': 2,
                                    'cpu_time_us': 796000,
                                    }
        mapping_power_stat.return_value = power_state.SHUTDOWN
        fake_inst = fake_instance.fake_instance_obj(self._context,
                    name='fake', power_state=power_state.PAUSED,
                    memory_mb='1024',
                    vcpus='4')
        inst_info = self.driver._get_instance_info(fake_inst)
        mapping_power_stat.assert_called_once_with('off')
        self.assertEqual(inst_info.state, power_state.SHUTDOWN)
        self.assertEqual(inst_info.mem_kb, 44)

    @mock.patch.object(driver.ZVMDriver, '_get_instance_info')
    def test_get_info(self, _get_instance_info):
        _fake_inst_info = hardware.InstanceInfo(state=0x01, mem_kb=131072,
                            num_cpu=4, cpu_time_ns=330528353,
                            max_mem_kb=1048576)
        _get_instance_info.return_value = _fake_inst_info
        fake_inst = fake_instance.fake_instance_obj(self._context,
                    name='fake', power_state=power_state.RUNNING,
                    memory_mb='1024',
                    vcpus='4')
        inst_info = self.driver.get_info(fake_inst)
        self.assertEqual(0x01, inst_info.state)
        self.assertEqual(131072, inst_info.mem_kb)
        self.assertEqual(4, inst_info.num_cpu)
        self.assertEqual(330528353, inst_info.cpu_time_ns)
        self.assertEqual(1048576, inst_info.max_mem_kb)

    @mock.patch.object(driver.ZVMDriver, '_get_instance_info')
    def test_get_info_instance_not_exist_error(self, _get_instance_info):
        _get_instance_info.side_effect = sdkexception.ZVMVirtualMachineNotExist
        fake_inst = fake_instance.fake_instance_obj(self._context,
                    name='fake', power_state=power_state.RUNNING,
                    memory_mb='1024',
                    vcpus='4')
        self.assertRaises(nova_exception.InstanceNotFound,
                          self.driver.get_info,
                          fake_inst)

    def test_get_available_nodes(self):
        nodes = self.driver.get_available_nodes()
        self.assertEqual(nodes[0], 'fakenode')

    @mock.patch.object(sdkapi.SDKAPI, 'guest_get_console_output')
    def test_get_console_output(self, gco):
        self.driver.get_console_output({}, self._instance)
        gco.assert_called_with('test0001')

    @mock.patch.object(sdkapi.SDKAPI, 'guest_start')
    @mock.patch.object(driver.ZVMDriver, '_wait_network_ready')
    @mock.patch.object(sdkapi.SDKAPI, 'guest_config_minidisks')
    @mock.patch.object(sdkapi.SDKAPI, 'guest_deploy')
    @mock.patch.object(driver.ZVMDriver, '_setup_network')
    @mock.patch.object(sdkapi.SDKAPI, 'guest_create')
    @mock.patch.object(zvmutils.ImageUtils, 'import_spawn_image')
    @mock.patch.object(sdkapi.SDKAPI, 'image_query')
    @mock.patch.object(zvmutils.VMUtils, 'generate_configdrive')
    @mock.patch.object(dist.LinuxDistManager, 'get_linux_dist')
    def _test_spawn(self, mock_linux_dist,
                    generate_configdrive, image_query, import_spawn_image,
                    guest_create, setup_network, guest_deploy,
                    guest_config_minidisks, wait_network_ready,
                    guest_start, image_query_result, eph_disks):
        generate_configdrive.return_value = '/tmp/fakecfg.tgz'
        image_query.side_effect = image_query_result

        self._block_device_info['ephemerals'] = eph_disks
        root_disk = {'size': '3g', 'is_boot_disk': True}
        disk_list = [root_disk]
        eph_list = []
        for eph in eph_disks:
            eph_dict = {'size': '%ig' % eph['size'],
                        'format': (eph['guest_format'] or
                                   CONF.default_ephemeral_format or
                                   const.DEFAULT_EPH_DISK_FMT)}
            eph_list.append(eph_dict)
        if eph_list:
            disk_list.extend(eph_list)

        os_distro = self._image_meta.properties.os_distro

        self.driver.spawn(self._context, self._instance, self._image_meta,
                          injected_files=None,
                          admin_password=None,
                          network_info=self._network_info,
                          block_device_info=self._block_device_info,
                          flavor=self._flavor)
        generate_configdrive.assert_called_once_with(self._context,
                                                     self._instance,
                                                     os_distro,
                                                     self._network_info,
                                                     None, None)
        image_query.assert_called_with(self._image_meta.id)
        if not image_query_result[0]:
            import_spawn_image.assert_called_once_with(self._context,
                                                       self._image_meta.id,
                                                       os_distro)

        guest_create.assert_called_once_with('test0001', 1, 1024, disk_list)
        setup_network.assert_called_once_with('test0001', self._network_info)
        guest_deploy.assert_called_once_with('test0001',
            'rhel7.2-s390x-netboot-0a0c576a_157f_42c8_bde5_2a254d8b77fc',
            '/tmp/fakecfg.tgz', zvmutils.get_host())

        if eph_disks:
            guest_config_minidisks.assert_called_once_with('test0001',
                                                           eph_list)

        wait_network_ready.assert_called_once_with('test0001',
                                                   self._instance)
        guest_start.assert_called_once_with('test0001')

    def test_spawn_simple_path(self):
        self._test_spawn(image_query_result=(
            ["rhel7.2-s390x-netboot-0a0c576a_157f_42c8_bde5_2a254d8b77fc"],
            ["rhel7.2-s390x-netboot-0a0c576a_157f_42c8_bde5_2a254d8b77fc"]),
            eph_disks=[])

    def test_spawn_image_not_in_backend(self):
        self._test_spawn(image_query_result=(
            [],
            ["rhel7.2-s390x-netboot-0a0c576a_157f_42c8_bde5_2a254d8b77fc"]),
            eph_disks=[])

    def test_spawn_with_ephemeral_disks(self):
        self._test_spawn(image_query_result=(
            ["rhel7.2-s390x-netboot-0a0c576a_157f_42c8_bde5_2a254d8b77fc"],
            ["rhel7.2-s390x-netboot-0a0c576a_157f_42c8_bde5_2a254d8b77fc"]),
            eph_disks=[{'guest_format': u'ext3',
                        'device_name': u'/dev/sdb',
                        'disk_bus': None,
                        'device_type': None,
                        'size': 1},
                       {'guest_format': u'ext4',
                        'device_name': u'/dev/sdc',
                        'disk_bus': None,
                        'device_type': None,
                        'size': 2}])

    @mock.patch.object(driver.ZVMDriver, '_instance_exists')
    @mock.patch.object(sdkapi.SDKAPI, 'guest_delete')
    def test_destroy(self, guest_delete, instance_exists):
        instance_exists.return_value = True
        self.driver.destroy(self._context, self._instance,
                            network_info=self._network_info)
        guest_delete.assert_called_once_with(self._instance['name'])

    @mock.patch.object(driver.ZVMDriver, '_instance_exists')
    @mock.patch.object(sdkapi.SDKAPI, 'guest_stop')
    def test_power_off(self, guest_stop, instance_exists):
        instance_exists.return_value = True
        self.driver.power_off(self._instance)
        guest_stop.assert_called_once_with(self._instance['name'], 0, 0)

    @mock.patch.object(driver.ZVMDriver, '_instance_exists')
    @mock.patch.object(sdkapi.SDKAPI, 'guest_start')
    def test_power_on(self, guest_start, instance_exists):
        instance_exists.return_value = True
        self.driver.power_on(self._context, self._instance,
                             network_info=self._network_info)
        guest_start.assert_called_once_with(self._instance['name'])
