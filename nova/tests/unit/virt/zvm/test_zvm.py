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

"""Test suite for ZVMDriver."""

import __builtin__
import httplib
import os
import socket

import mock
import mox
from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_serialization import jsonutils
from oslo_utils import fileutils

from nova.compute import power_state
from nova import context
from nova import db
from nova import exception as nova_exception
from nova.i18n import _
from nova.image import glance
from nova.network import model
from nova import test
from nova.tests.unit import fake_instance
from nova.virt import fake
from nova.virt import hardware
from nova.virt.zvm import configdrive
from nova.virt.zvm import const
from nova.virt.zvm import dist
from nova.virt.zvm import driver
from nova.virt.zvm import exception
from nova.virt.zvm import imageop
from nova.virt.zvm import instance
from nova.virt.zvm import networkop
from nova.virt.zvm import utils as zvmutils
from nova.virt.zvm import volumeop


CONF = cfg.CONF


class FakeXCATConn(object):

    def __init__(self):
        pass

    def request(self, one, two, three=None, four={}):
        pass


class FakeHTTPResponse(object):

    def __init__(self, status=None, reason=None, data=None):
        self.status = status
        self.reason = reason
        self.data = data

    def read(self):
        return self.data


class FakeImageService(object):

    def __init__(self, values):
        self.values = values

    def show(self, *args):
        return self.values

    def delete(self, *args):
        pass

    def update(self, *args):
        pass


class FakeInstMeta(object):

    def metadata_for_config_drive(self):
        return [('openstack', 'data1'), ('ec2', 'data2')]


class ZVMTestCase(test.TestCase):
    """Base testcase class of zvm driver and zvm instance."""

    def setUp(self):
        super(ZVMTestCase, self).setUp()
        self.context = context.get_admin_context()
        self.instance = db.instance_create(self.context,
                                 {'user_id': 'fake',
                                  'project_id': 'fake',
                                  'instance_type_id': 1,
                                  'memory_mb': 1024,
                                  'vcpus': 2,
                                  'root_gb': 10,
                                  'ephemeral_gb': 0,
                                  'image_ref': '0000-1111',
                                  'host': 'fakenode'})
        self.instance2 = db.instance_create(self.context,
                                 {'user_id': 'fake',
                                  'project_id': 'fake',
                                  'instance_type_id': 1,
                                  'memory_mb': 1024,
                                  'vcpus': 2,
                                  'root_gb': 10,
                                  'ephemeral_gb': 1,
                                  'image_ref': '0000-1111',
                                  'host': 'fakenode'})
        self.flags(host='fakehost',
                   my_ip='10.1.1.10',
                   zvm_xcat_server='10.10.10.10',
                   zvm_xcat_username='fake',
                   zvm_xcat_password='fake',
                   zvm_host='fakenode',
                   zvm_diskpool='fakedp',
                   zvm_diskpool_type='FBA',
                   instance_name_template='os%06x',
                   zvm_xcat_master='fakemn',
                   zvm_scsi_pool='fakesp',
                   network_api_class='nova.network.neutronv2.api.API',
                   zvm_image_default_password='pass',
                   zvm_fcp_list="1FB0-1FB3",
                   zvm_zhcp_fcp_list="1FAF",
                   config_drive_format='tgz',
                   zvm_image_compression_level='0')

    def tearDown(self):
        self.addCleanup(self.stubs.UnsetAll)
        self.mox.UnsetStubs()
        super(ZVMTestCase, self).tearDown()

    def _set_fake_xcat_responses(self, fake_resp_list):
        self.stubs.Set(zvmutils, "XCATConnection", FakeXCATConn)
        self.mox.StubOutWithMock(zvmutils.XCATConnection, 'request')
        for res_data in fake_resp_list:
            res = {'message': jsonutils.dumps(res_data)}
            zvmutils.XCATConnection.request(mox.IgnoreArg(), mox.IgnoreArg(),
                mox.IgnoreArg(), mox.IgnoreArg()).AndReturn(res)
        self.mox.ReplayAll()

    def _set_fake_xcat_resp(self, fake_resp_list):
        """fake_resq_list:
                [(method, url, body, response_data),
                 (method, url, body, response_data),
                 ...,
                 (method, url, body, response_data)]
        """
        self.mox.StubOutWithMock(zvmutils.XCATConnection, 'request')
        for res in fake_resp_list:
            method = res[0] or mox.IgnoreArg()
            url = res[1] or mox.IgnoreArg()
            body = res[2] or mox.IgnoreArg()
            res = {'message': jsonutils.dumps(res[3])}
            zvmutils.XCATConnection.request(method, url, body,
                                            mox.IgnoreArg()).AndReturn(res)
        self.mox.ReplayAll()

    def _gen_resp(self, **kwargs):
        data = []
        for (k, v) in kwargs.iteritems():
            if v not in (None, []):
                data.append({k: v})

        return {'data': data}

    def _generate_xcat_resp(self, info):
        return {'data': [{'info': info}]}

    def _fake_fun(self, value=None):
        return lambda *args, **kwargs: value

    def _app_auth(self, url):
        return ''.join([url, '?userName=fake&password=fake&format=json'])


class ZVMDriverTestCases(ZVMTestCase):
    """Unit tests for z/VM driver methods."""

    fake_image_meta = {
        'checksum': '1a2bbbdbcc9c536a2688fc6278685dfb',
        'container_format': 'bare',
        'disk_format': 'raw',
        'id': 'bef39792-1ae2-46f5-b44c-0641bfcb3b98',
        'is_public': False,
        'name': 'fakeimg',
        'properties': {'architecture': 's390x',
                       'image_file_name': 'abc',
                       'image_type': 'linux',
                       'os_name': 'Linux',
                       'os_version': 'rhel6.2',
                       'provisioning_method': 'netboot',
                       'image_type_xcat': 'linux'},
        'size': 578181045,
        'status': 'active'}

    keys = ('image_ref', 'uuid', 'user_id', 'project_id',
            'power_state', 'system_metadata', 'memory_mb', 'vcpus',
            'root_gb', 'ephemeral_gb')
    _fake_inst = {'name': 'os000001'}
    _old_inst = {'name': 'rszos000001'}
    for k in keys:
        _fake_inst[k] = ''
        _old_inst[k] = ''

    def _fake_host_rinv_info(self):
        fake_host_rinv_info = ["fakenode: z/VM Host: FAKENODE\n"
                               "fakenode: zHCP: fakehcp.fake.com\n"
                               "fakenode: CEC Vendor: FAKE\n"
                               "fakenode: CEC Model: 2097\n"
                               "fakenode: Hypervisor OS: z/VM 6.1.0\n"
                               "fakenode: Hypervisor Name: fakenode\n"
                               "fakenode: Architecture: s390x\n"
                               "fakenode: LPAR CPU Total: 10\n"
                               "fakenode: LPAR CPU Used: 10\n"
                               "fakenode: LPAR Memory Total: 16G\n"
                               "fakenode: LPAR Memory Offline: 0\n"
                               "fakenode: LPAR Memory Used: 16.0G\n"
                               "fakenode: IPL Time:"
                               "IPL at 03/13/14 21:43:12 EDT\n"]
        return self._generate_xcat_resp(fake_host_rinv_info)

    def _fake_disk_info(self):
        fake_disk_info = ["fakenode: FAKEDP Total: 406105.3 G\n"
                          "fakenode: FAKEDP Used: 367262.6 G\n"
                          "fakenode: FAKEDP Free: 38842.7 G\n"]
        return self._generate_xcat_resp(fake_disk_info)

    def _set_fake_host_info(self):
        self._set_fake_xcat_responses([self._fake_host_rinv_info(),
            self._fake_disk_info(), self._gen_resp(info=['userid=fakehcp'])])

    def _setup_fake_inst_obj(self):
        keys = ('image_ref', 'uuid', 'user_id', 'project_id',
            'power_state', 'system_metadata', 'memory_mb', 'vcpus',
            'root_gb', 'ephemeral_gb')
        self._fake_inst = {'name': 'os000001'}
        self._old_inst = {'name': 'rszos000001'}
        for k in keys:
            self._fake_inst[k] = ''
            self._old_inst[k] = ''

    def setUp(self):
        super(ZVMDriverTestCases, self).setUp()
        self._set_fake_host_info()
        self._setup_fake_inst_obj()
        self.driver = driver.ZVMDriver(fake.FakeVirtAPI())
        self.mox.UnsetStubs()

    def test_init_driver(self):
        self.assertIsInstance(self.driver._xcat_url, zvmutils.XCATUrl)
        self.assertIsInstance(self.driver._zvm_images, imageop.ZVMImages)
        self.assertIsInstance(self.driver._pathutils, zvmutils.PathUtils)
        self.assertIsInstance(self.driver._networkop,
                              networkop.NetworkOperator)

    def test_update_host_info(self):
        self._set_fake_xcat_responses([self._fake_host_rinv_info(),
                                       self._fake_disk_info()])
        host_info = self.driver.update_host_status()[0]
        self.mox.VerifyAll()
        self.assertEqual(host_info['hypervisor_hostname'], 'fakenode')
        self.assertEqual(host_info['host_memory_total'], 16 * 1024)

    def _fake_instance_list_data(self):
        return {'data': [{'data': self._fake_instances_list()}]}

    def _fake_instances_list(self):
        inst_list = ['#node,hcp,userid,nodetype,parent,comments,disable',
                     '"fakehcp","fakehcp.fake.com","HCP","vm","fakenode"',
                     '"fakenode","fakehcp.fake.com",,,,,',
                     '"os000001","fakehcp.fake.com","OS000001",,,,']
        return inst_list

    def test_list_instances(self):
        self._set_fake_xcat_responses([self._fake_instance_list_data()])
        inst_list = self.driver.list_instances()
        self.mox.VerifyAll()
        self.assertTrue("os000001" in inst_list)

    def test_list_instances_exclude_xcat_master(self):
        self.flags(zvm_xcat_master='xcat')
        fake_inst_list = self._fake_instances_list()
        fake_inst_list.append('"xcat","fakexcat.fake.com",,,,,')
        self._set_fake_xcat_responses([
            {'data': [{'data': fake_inst_list}]}])
        inst_list = self.driver.list_instances()
        self.mox.VerifyAll()
        self.assertTrue("os000001" in inst_list)
        self.assertTrue("xcat" not in inst_list)

    def test_get_available_resource(self):
        self._set_fake_xcat_responses([self._fake_host_rinv_info(),
                                       self._fake_disk_info()])
        res = self.driver.get_available_resource('fakenode')
        self.mox.VerifyAll()
        self.assertEqual(res['vcpus'], 10)
        self.assertEqual(res['memory_mb_used'], 16 * 1024)
        self.assertEqual(res['disk_available_least'], 38843)

    def _fake_instance_info(self):
        inst_inv_info = [
            "os000001: Uptime: 4 days 20 hr 00 min\n"
            "os000001: CPU Used Time: 330528353\n"
            "os000001: Total Memory: 128M\n"
            "os000001: Max Memory: 2G\n"
            "os000001: \n"
            "os000001: Processors: \n"
            "os000001:     CPU 03  ID  FF00EBBE20978000 CP  CPUAFF ON\n"
            "os000001:     CPU 00  ID  FF00EBBE20978000 (BASE) CP  CPUAFF ON\n"
            "os000001:     CPU 01  ID  FF00EBBE20978000 CP  CPUAFF ON\n"
            "os000001:     CPU 02  ID  FF00EBBE20978000 CP  CPUAFF ON\n"
            "os000001: \n"
            ]
        return self._generate_xcat_resp(inst_inv_info)

    def _fake_reachable_data(self, stat):
        return {"data": [{"node": [{"name": ["os000001"], "data": [stat]}]}]}

    @mock.patch('nova.virt.zvm.instance.ZVMInstance.get_info')
    def test_get_info(self, mk_get_info):
        _fake_inst_obj = hardware.InstanceInfo(state=0x01, mem_kb=131072,
                            num_cpu=4, cpu_time_ns=330528353,
                            max_mem_kb=1048576)
        mk_get_info.return_value = _fake_inst_obj

        inst_info = self.driver.get_info(self.instance)
        self.assertEqual(0x01, inst_info.state)
        self.assertEqual(131072, inst_info.mem_kb)
        self.assertEqual(4, inst_info.num_cpu)
        self.assertEqual(330528353, inst_info.cpu_time_ns)
        self.assertEqual(1048576, inst_info.max_mem_kb)

    @mock.patch('nova.virt.zvm.exception.ZVMXCATRequestFailed.format_message')
    @mock.patch('nova.virt.zvm.exception.ZVMXCATRequestFailed.msg_fmt')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance.get_info')
    def test_get_info_not_exist(self, mk_get_info, mk_msg_fmt, mk_fm):
        mk_get_info.side_effect = exception.ZVMXCATRequestFailed
        _emsg = "Forbidden Invalid nodes and/or groups"
        mk_msg_fmt.return_value = _emsg
        mk_fm.return_value = _emsg

        self.assertRaises(nova_exception.InstanceNotFound,
                          self.driver.get_info, self.instance)

    @mock.patch('nova.virt.zvm.exception.ZVMXCATRequestFailed.msg_fmt')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance.get_info')
    def test_get_info_other_err(self, mk_get_info, mk_msg_fmt):
        mk_get_info.side_effect = exception.ZVMXCATRequestFailed
        mk_msg_fmt.return_value = "Other error"

        self.assertRaises(exception.ZVMXCATRequestFailed,
                          self.driver.get_info, self.instance)

    def test_destroy(self):
        rmvm_info = ["os000001: Deleting virtual server OS000001... Done"]
        fake_resp_list = [
            ("GET", None, None, self._fake_instance_list_data()),
            ("DELETE", None, None, self._gen_resp(info=rmvm_info))]
        self._set_fake_xcat_resp(fake_resp_list)
        self.driver.destroy({}, self.instance, {}, {})
        self.mox.VerifyAll()

    def test_destroy_failed(self):
        rmvm_info = ["os000001: Deleting virtual server OS000001... Failed"]
        det_res = self._gen_resp(info=rmvm_info, error=['error'])
        fake_resp_list = [
            ("GET", None, None, self._fake_instance_list_data()),
            ("DELETE", None, None, det_res)]
        self._set_fake_xcat_resp(fake_resp_list)
        self.assertRaises(exception.ZVMXCATInternalError,
                          self.driver.destroy, {}, self.instance, {}, {})
        self.mox.VerifyAll()

    def test_destroy_non_exist(self):
        self._set_fake_xcat_responses([self._fake_instance_list_data()])
        self.driver.destroy({}, self.instance2, {}, {})
        self.mox.VerifyAll()

    def _fake_image_meta(self):
        return {'checksum': '1a2bbbdbcc9c536a2688fc6278685dfb',
                'container_format': 'bare',
                'disk_format': 'raw',
                'id': 'bef39792-1ae2-46f5-b44c-0641bfcb3b98',
                'is_public': False,
                'name': 'fakeimg',
                'properties': {'architecture': 's390x',
                               'image_file_name': 'abc',
                               'image_type_xcat': 'linux',
                               'os_name': 'Linux',
                               'os_version': 'rhel6.2',
                               'provisioning_method': 'netboot',
                               'root_disk_units': 578181045,
                               },
                'size': 578181045,
                'status': 'active',
                'min_disk': 3,
                }

    def _fake_network_info(self):
        info = [
            (
                {'ovs_interfaceid': '6ef5433c-f29b-4bcc-b8c5-f5159d7e05ba',
                 'network': (
                    {'bridge': 'br-int',
                     'subnets': [({
                        'ips': [({'meta': {},
                                  'version': 4,
                                  'type': 'fixed',
                                  'floating_ips': [],
                                  'address': '10.1.11.51'
                                })],
                        'version': 4,
                        'meta': {},
                        'dns': [],
                        'routes': [],
                        'cidr': '10.1.0.0/16',
                        'gateway': ({'meta': {},
                                     'version': 4,
                                     'type': 'gateway',
                                     'address': u'10.1.0.1'
                                    })
                                   })],
                     'meta': {'injected': False,
                              'tenant_id': '0be9e98fdf6d4f599632226154c6c86c'},
                     'id': '01921c21-0373-4ccf-934e-9c6b2ccd7bdc',
                     'label': u'xcat_management'
                     }
                             ),
                 'devname': 'tap6ef5433c-f2',
                 'qbh_params': None,
                 'meta': {},
                 'address': '02:00:00:ee:ae:51',
                 'type': 'ovs',
                 'id': '6ef5433c-f29b-4bcc-b8c5-f5159d7e05ba',
                 'qbg_params': None
                }
            )
        ]
        return info

    def test_spawn(self):
        self.instance['config_drive'] = True
        self.stubs.Set(self.driver._pathutils, 'get_instance_path',
                       self._fake_fun('/temp/os000001'))
        self.stubs.Set(self.driver, '_create_config_drive',
                       self._fake_fun('/temp/os000001/configdrive.tgz'))
        self.stubs.Set(instance.ZVMInstance, 'create_xcat_node',
                       self._fake_fun())
        self.stubs.Set(self.driver, '_preset_instance_network',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'image_exist_xcat',
                       self._fake_fun(False))
        self.stubs.Set(self.driver, '_import_image_to_xcat', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'create_userid', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'update_node_info',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'get_imgname_xcat',
                       self._fake_fun('fakeimg'))
        self.stubs.Set(dist.LinuxDist,
                       "create_network_configuration_files",
                       self._fake_fun((['fakefile', 'fakecmd'])))
        self.stubs.Set(instance.ZVMInstance, 'deploy_node', self._fake_fun())
        self.stubs.Set(self.driver._pathutils, 'clean_temp_folder',
                       self._fake_fun())
        self.stubs.Set(self.driver._networkop, 'create_nic', self._fake_fun())
        self.stubs.Set(zvmutils, 'punch_adminpass_file', self._fake_fun())
        self.stubs.Set(zvmutils, 'punch_xcat_auth_file', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'power_on', self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'update_last_use_date',
                       self._fake_fun())
        self.stubs.Set(self.driver, '_wait_for_addnic', self._fake_fun())
        self.stubs.Set(self.driver, '_is_nic_granted', self._fake_fun(True))
        self.driver.spawn({}, self.instance, self._fake_image_meta(), ['fake'],
                          'fakepass', self._fake_network_info(), {})

    def test_spawn_with_eph(self):
        self.instance['config_drive'] = True
        self.stubs.Set(self.driver._pathutils, 'get_instance_path',
                       self._fake_fun('/temp/os000001'))
        self.stubs.Set(self.driver, '_create_config_drive',
                       self._fake_fun('/temp/os000001/configdrive.tgz'))
        self.stubs.Set(instance.ZVMInstance, 'create_xcat_node',
                       self._fake_fun())
        self.stubs.Set(self.driver, '_preset_instance_network',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'image_exist_xcat',
                       self._fake_fun(False))
        self.stubs.Set(self.driver, '_import_image_to_xcat', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'create_userid', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'update_node_info',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'get_imgname_xcat',
                       self._fake_fun('fakeimg'))
        self.stubs.Set(dist.LinuxDist,
                       "create_network_configuration_files",
                       self._fake_fun((['fakefile', 'fakecmd'])))
        self.stubs.Set(instance.ZVMInstance, 'deploy_node', self._fake_fun())
        self.stubs.Set(self.driver._pathutils, 'clean_temp_folder',
                       self._fake_fun())
        self.stubs.Set(self.driver._networkop, 'create_nic', self._fake_fun())
        self.stubs.Set(zvmutils, 'punch_adminpass_file', self._fake_fun())
        self.stubs.Set(zvmutils, 'punch_xcat_auth_file', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'power_on', self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'update_last_use_date',
                       self._fake_fun())
        self.stubs.Set(self.driver, '_wait_for_addnic', self._fake_fun())
        self.stubs.Set(self.driver, '_is_nic_granted', self._fake_fun(True))
        self.stubs.Set(__builtin__, 'open', mock.mock_open())
        self.stubs.Set(os, 'remove', self._fake_fun())
        self.stubs.Set(zvmutils, 'get_host', self._fake_fun("fake@10.1.1.10"))
        self._set_fake_xcat_resp([
            ("PUT", None, None, self._gen_resp(info='done')),
            ])
        self.driver.spawn({}, self.instance2, self._fake_image_meta(),
                          ['fake'], 'fakepass', self._fake_network_info(), {})
        self.mox.VerifyAll()

    def test_spawn_with_eph_opts(self):
        self.instance['config_drive'] = True
        self.instance['ephemeral_gb'] = 2
        network_info = self._fake_network_info()
        image_meta = self._fake_image_meta()
        fake_bdi = {'ephemerals': [
            {'device_name': '/dev/sdb',
             'device_type': None,
             'disk_bus': None,
             'guest_format': u'ext4',
             'size': 2},
            {'device_name': '/dev/sdc',
             'device_type': None,
             'disk_bus': None,
             'guest_format': u'ext3',
             'size': 1}]}

        self.mox.StubOutWithMock(self.driver._pathutils, 'get_instance_path')
        self.mox.StubOutWithMock(dist.LinuxDist,
            "create_network_configuration_files")
        self.mox.StubOutWithMock(self.driver, '_create_config_drive')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_xcat_node')
        self.mox.StubOutWithMock(self.driver, '_preset_instance_network')
        self.mox.StubOutWithMock(self.driver._zvm_images, 'image_exist_xcat')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_userid')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'update_node_info')
        self.mox.StubOutWithMock(self.driver._networkop, 'create_nic')
        self.mox.StubOutWithMock(self.driver._zvm_images, 'get_imgname_xcat')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'deploy_node')
        self.mox.StubOutWithMock(self.driver._pathutils, 'clean_temp_folder')
        self.mox.StubOutWithMock(zvmutils, 'punch_adminpass_file')
        self.mox.StubOutWithMock(zvmutils, 'punch_xcat_auth_file')
        self.mox.StubOutWithMock(zvmutils, 'process_eph_disk')
        self.mox.StubOutWithMock(self.driver, '_wait_for_addnic')
        self.mox.StubOutWithMock(self.driver, '_is_nic_granted')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'power_on')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'update_last_use_date')

        self.driver._pathutils.get_instance_path('fakenode',
            'os000001').AndReturn('/temp/os000001')
        dist.LinuxDist.create_network_configuration_files(
            mox.IgnoreArg(), network_info, mox.IgnoreArg()
            ).AndReturn(('/tmp/fakefile', 'fakecmd'))
        self.driver._create_config_drive('/temp/os000001', self.instance,
            mox.IgnoreArg(), mox.IgnoreArg(), mox.IgnoreArg(),
            mox.IgnoreArg()).AndReturn('/temp/os000001/configdrive.tgz')
        instance.ZVMInstance.create_xcat_node('fakehcp.fake.com')
        self.driver._preset_instance_network('os000001', network_info)
        self.driver._zvm_images.image_exist_xcat(mox.IgnoreArg()).AndReturn(
                                                                    True)
        instance.ZVMInstance.create_userid(fake_bdi,
            image_meta).AndReturn(None)
        instance.ZVMInstance.update_node_info(image_meta)
        self.driver._networkop.create_nic(mox.IgnoreArg(), 'os000001',
            mox.IgnoreArg(), mox.IgnoreArg(), '1000')
        self.driver._zvm_images.get_imgname_xcat(mox.IgnoreArg()).AndReturn(
                                                                    'fakeimg')
        instance.ZVMInstance.deploy_node('fakeimg',
                                         '/temp/os000001/configdrive.tgz')
        zvmutils.punch_adminpass_file(mox.IgnoreArg(), 'os000001', 'pass',
                                      mox.IgnoreArg())
        zvmutils.punch_xcat_auth_file(mox.IgnoreArg(), 'os000001')
        zvmutils.process_eph_disk('os000001', mox.IgnoreArg(), mox.IgnoreArg(),
                                  mox.IgnoreArg())
        zvmutils.process_eph_disk('os000001', mox.IgnoreArg(), mox.IgnoreArg(),
                                  mox.IgnoreArg())
        self.driver._wait_for_addnic('os000001').AndReturn(True)
        self.driver._is_nic_granted('os000001').AndReturn(True)
        instance.ZVMInstance.power_on()
        self.driver._pathutils.clean_temp_folder(mox.IgnoreArg())
        self.driver._zvm_images.update_last_use_date(mox.IgnoreArg())
        self.mox.ReplayAll()

        self.driver.spawn(self.context, self.instance, self._fake_image_meta(),
            ['fake'], 'fakepass', self._fake_network_info(), fake_bdi)
        self.mox.VerifyAll()

    def test_spawn_image_error(self):
        self.stubs.Set(self.driver._pathutils, 'get_instance_path',
                       self._fake_fun('/temp/os000001'))
        self.stubs.Set(self.driver, '_create_config_drive',
                       self._fake_fun('/temp/os000001/configdrive.tgz'))
        self.stubs.Set(instance.ZVMInstance, 'create_xcat_node',
                       self._fake_fun())
        self.stubs.Set(self.driver, '_preset_instance_network',
                       self._fake_fun())
        self.stubs.Set(dist.LinuxDist,
                       "create_network_configuration_files",
                       self._fake_fun(('/tmp/fakefile', 'fakecmd')))
        self.mox.StubOutWithMock(self.driver._zvm_images, 'image_exist_xcat')
        self.driver._zvm_images.image_exist_xcat(mox.IgnoreArg()
                                ).AndRaise(exception.ZVMImageError(msg='fake'))
        self.mox.ReplayAll()
        self.stubs.Set(instance.ZVMInstance, 'delete_xcat_node',
                       self._fake_fun())
        self.assertRaises(exception.ZVMImageError, self.driver.spawn, {},
                          self.instance, self._fake_image_meta(), [],
                          'fakepass', self._fake_network_info(), {})
        self.mox.VerifyAll()

    def test_spawn_deploy_failed(self):
        self.stubs.Set(self.driver._pathutils, 'get_instance_path',
                       self._fake_fun('/temp/os000001'))
        self.stubs.Set(instance.ZVMInstance, 'create_xcat_node',
                       self._fake_fun())
        self.stubs.Set(self.driver, '_preset_instance_network',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'image_exist_xcat',
                       self._fake_fun(False))
        self.stubs.Set(dist.LinuxDist,
                       "create_network_configuration_files",
                       self._fake_fun(("/tmp/fakefile", "fakecmd")))
        self.stubs.Set(self.driver, '_import_image_to_xcat', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'create_userid', self._fake_fun())
        self.stubs.Set(self.driver._networkop, 'create_nic', self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'update_node_info',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'get_imgname_xcat',
                       self._fake_fun('fakeimg'))
        self.mox.StubOutWithMock(instance.ZVMInstance, 'deploy_node')
        instance.ZVMInstance.deploy_node(mox.IgnoreArg(), mox.IgnoreArg()
            ).AndRaise(exception.ZVMXCATDeployNodeFailed(
            node='os000001', msg='fake'))
        self.mox.ReplayAll()
        self.stubs.Set(self.driver._pathutils, 'clean_temp_folder',
                       self._fake_fun())
        self.stubs.Set(self.driver, 'destroy', self._fake_fun())
        self.assertRaises(exception.ZVMXCATDeployNodeFailed, self.driver.spawn,
                          {}, self.instance, self._fake_image_meta(), [],
                          'fakepass', self._fake_network_info(), {})
        self.mox.VerifyAll()

    def _set_reachable(self, stat):
        return {"data": [{"node": [{"name": ["os000001"],
                                    "data": [stat]}]}]}

    def test_power_on(self):
        info = ["os000001: Activating OS000001... Done\n"]
        self._set_fake_xcat_responses([self._generate_xcat_resp(info),
                                       self._set_reachable('sshd')])
        self.driver.power_on({}, self.instance, {})

    def _fake_manifest(self):
        return {'imagename': 'fakeimg',
                'imagetype': 'raw',
                'osarch': 's390x',
                'osname': 'fakeos',
                'osvers': 'fakev',
                'profile': 'fakeprof',
                'provmethod': 'netboot'}

    def test_snapshot(self):
        if not os.path.exists("/tmp/fakeimg"):
            os.mknod("/tmp/fakeimg")
        self.instance['power_state'] = 0x01
        self.stubs.Set(glance, 'get_remote_image_service',
            self._fake_fun((FakeImageService(self.fake_image_meta), 0)))
        url_fspace = ''.join([self._app_auth("/xcatws/nodes/fakemn/inventory"),
                                      "&field=--freerepospace"])
        res_fspace = self._gen_resp(info=["gpok164: Free Image "
                                          "Repository: 13.9G"])
        url_xdsh = self._app_auth('/xcatws/nodes/os000001/dsh')
        body_cmd = ["command=df -h /"]
        res_img_need = self._gen_resp(data=["Filesystem Size Used Avail Use% "
                                            "Mounted on /dev/dasda1 6.8G "
                                            "5.2G 1.3G  81% /"])
        self._set_fake_xcat_resp([
            ("GET", url_fspace, None, res_fspace),
            ('PUT', url_xdsh, body_cmd, res_img_need),
            ])
        self.stubs.Set(self.driver._zvm_images, 'create_zvm_image',
                       self._fake_fun(''))
        self.stubs.Set(self.driver, 'power_on', self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'get_image_from_xcat',
                       self._fake_fun(('/tmp', 'fakeimg.tgz')))
        self.stubs.Set(self.driver._zvm_images, 'delete_image_from_xcat',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'update_last_use_date',
                       self._fake_fun())
        self.stubs.Set(self.driver, '_is_shared_image_repo',
                       self._fake_fun(False))
        self.stubs.Set(self.driver._zvm_images, 'untar_image_bundle',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'parse_manifest_xml',
                       self._fake_fun(self._fake_manifest()))
        self.stubs.Set(self.driver._zvm_images, 'get_image_file_name',
                       self._fake_fun('fakeimg'))
        self.stubs.Set(self.driver._zvm_images, 'clean_up_snapshot_time_path',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images, 'get_root_disk_units',
                       self._fake_fun(1111))
        self.stubs.Set(os, 'makedirs', self._fake_fun())
        self.stubs.Set(__builtin__, 'open', mock.mock_open())
        self.driver.snapshot({}, self.instance, '0000-1111', self._fake_fun())
        self.mox.VerifyAll()

    def test_snapshot_capture_failed(self):
        self.instance['power_state'] = 0x04
        self.stubs.Set(self.driver, 'power_on', self._fake_fun())
        self.stubs.Set(glance, 'get_remote_image_service',
            self._fake_fun((FakeImageService(self._fake_image_meta()), 0)))
        self.stubs.Set(self.driver._zvm_images, 'get_free_space_xcat',
                       self._fake_fun(20.0))
        self.stubs.Set(self.driver._zvm_images, 'get_imgcapture_needed',
                       self._fake_fun(10.0))
        self.mox.StubOutWithMock(self.driver._zvm_images, 'create_zvm_image')
        self.driver._zvm_images.create_zvm_image(mox.IgnoreArg(),
            mox.IgnoreArg(), mox.IgnoreArg()).AndRaise(
            exception.ZVMImageError(msg='fake'))
        self.mox.ReplayAll()
        self.stubs.Set(self.driver._zvm_images, 'delete_image_glance',
                       self._fake_fun())
        self.assertRaises(exception.ZVMImageError, self.driver.snapshot,
                          {}, self.instance, '0000-1111', self._fake_fun())
        self.mox.VerifyAll()

    def test_snapshot_all_in_one_mode(self):
        fake_menifest = {'imagetype': 'linux',
                         'osarch': 's390x',
                         'imagename': 'fakeimg-uuid',
                         'osname': 'Linux',
                         'osvers': 'rhel65',
                         'profile': 'fakeimg-uuid',
                         'provmethod': 'netboot'}
        self.instance['power_state'] = 0x01

        self.stubs.Set(glance, 'get_remote_image_service',
            self._fake_fun((FakeImageService(self.fake_image_meta), 0)))
        self.stubs.Set(__builtin__, 'open', mock.mock_open())

        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'get_free_space_xcat')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'get_imgcapture_needed')
        self.mox.StubOutWithMock(self.driver._zvm_images, 'create_zvm_image')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'update_last_use_date')
        self.mox.StubOutWithMock(self.driver, '_reset_power_state')
        self.mox.StubOutWithMock(self.driver, '_is_shared_image_repo')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'get_image_file_path_from_image_name')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'get_image_file_name')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'get_root_disk_units')
        self.mox.StubOutWithMock(self.driver._zvm_images, 'get_image_menifest')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'delete_image_from_xcat')
        self.driver._zvm_images.get_free_space_xcat(
                        CONF.xcat_free_space_threshold,
                        CONF.zvm_xcat_master).AndReturn(10.0)
        self.driver._zvm_images.get_imgcapture_needed(
                                                self.instance).AndReturn(5.0)
        self.driver._zvm_images.create_zvm_image(self.instance, 'fakeimg',
                                            'uuid').AndReturn('fakeimg-uuid')
        self.driver._zvm_images.update_last_use_date('fakeimg-uuid')
        self.driver._reset_power_state(0x01, self.instance)
        self.driver._is_shared_image_repo('fakeimg-uuid').AndReturn(True)
        self.driver._zvm_images.get_image_file_path_from_image_name(
            'fakeimg-uuid').AndReturn('/install/netboot/rh/s390/fakeimg-uuid')
        self.driver._zvm_images.get_image_file_name(
            '/install/netboot/rh/s390/fakeimg-uuid').AndReturn('fakeimg.img')
        self.driver._zvm_images.get_root_disk_units(
            '/install/netboot/rh/s390/fakeimg-uuid/fakeimg.img').AndReturn(999)
        self.driver._zvm_images.get_image_menifest('fakeimg-uuid').AndReturn(
                                                                fake_menifest)
        self.driver._zvm_images.delete_image_from_xcat(mox.IgnoreArg())
        self.mox.ReplayAll()

        self.driver.snapshot(self.context, self.instance, 'uuid',
                             self._fake_fun())
        self.mox.VerifyAll()

    def test_reboot_hard(self):
        info1 = ["os000001: Shutting down... Done"]
        info2 = ["os000001: Activating... Done"]
        data = {"data": [{"info": info1}, {"info": info2}]}
        self._set_fake_xcat_responses([data, self._set_reachable('sshd')])
        self.driver.reboot(self.context, self.instance, {}, "HARD")
        self.mox.VerifyAll()

    def test_reboot_hard_from_down(self):
        info1 = ["os000001: Shutting down... Failed"]
        info2 = ["os000001: Activating... Done"]
        data = {"data": [{"info": info1}, {"info": info2}]}
        self._set_fake_xcat_responses([data, self._set_reachable('sshd')])
        self.driver.reboot(self.context, self.instance, {}, "HARD")
        self.mox.VerifyAll()

    def test_reboot_hard_failed(self):
        info1 = ["os000001: Shutting down... Failed"]
        info2 = ["os000001: Activating... Failed"]
        self._set_fake_xcat_responses([self._gen_resp(info=[info1, info2],
                                                      error=['(error)'])])
        self.assertRaises(exception.ZVMXCATInternalError,
                          self.driver.reboot,
                          self.context, self.instance, {}, "HARD")

    def test_reboot_soft(self):
        info1 = ["os000001: Shutting down... Done"]
        info2 = ["os000001: Activating... Done"]
        data = {"data": [{"info": info1}, {"info": info2}]}
        self._set_fake_xcat_responses([data, self._set_reachable('sshd')])
        self.driver.reboot(self.context, self.instance, {}, "SOFT")
        self.mox.VerifyAll()

    def test_reboot_soft_failed(self):
        info1 = ["os000001: Shutting down... Failed\n"]
        info2 = ["os000001: Activating... Failed\n"]
        self._set_fake_xcat_responses([self._gen_resp(info=[info1, info2],
                                                      error=['(error)'])])
        self.assertRaises(exception.ZVMXCATInternalError,
                          self.driver.reboot,
                          self.context, self.instance, {}, "SOFT")

    def test_power_off(self):
        info = ["os000001: Stopping OS000001... Done\n"]
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self.driver.power_off(self.instance, 10, 10)
        self.mox.VerifyAll()

    def test_power_off_inactive(self):
        info = ["os000001: Stopping LINUX171... Failed\n"
                "  Return Code: 200\n"
                "  Reason Code: 12\n"
                "  Description: Image not active\n"]
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self.driver.power_off(self.instance)
        self.mox.VerifyAll()

    def test_power_off_failed(self):
        info = ["os000001: Stopping OS000001... Failed\n"]
        self._set_fake_xcat_responses([self._gen_resp(info=info,
                                                      error=['error'])])
        self.assertRaises(nova_exception.InstancePowerOffFailure,
                          self.driver.power_off, self.instance)

    def _fake_connection_info(self):
        conn_info = {
            'driver_volume_type': 'fibre_channel',
            'data': {'volume_name': 'fakev',
                     'volume_id': 1,
                     'volume_type': '',
                     'volume_status': 'online',
                     'volume_size': 10,
                     'provider_location': '500507630B40C038:401340C900000000',
                     'zvm_fcp': 'F000',
                     'target_lun': '442',
                     'target_wwn': '401340C900000000',
                     'host': 'fakehost',
                     }
            }
        return conn_info

    def test_attach_volume(self):
        self.mox.StubOutWithMock(self.driver, '_format_mountpoint')
        self.mox.StubOutWithMock(self.driver, 'instance_exists')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'is_reachable')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'attach_volume')
        self.driver._format_mountpoint('/dev/sdd').AndReturn('/dev/vdd')
        self.driver.instance_exists('os000001').AndReturn(True)
        instance.ZVMInstance.is_reachable().AndReturn(True)
        instance.ZVMInstance.attach_volume(self.driver._volumeop, {},
                                           self._fake_connection_info(),
                                           self.instance, '/dev/vdd', True)
        self.mox.ReplayAll()

        self.driver.attach_volume({}, self._fake_connection_info(),
                                  self.instance, '/dev/sdd')
        self.mox.VerifyAll()

    def test_attach_volume_no_mountpoint(self):
        self.mox.StubOutWithMock(self.driver, 'instance_exists')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'is_reachable')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'attach_volume')
        self.driver.instance_exists('os000001').AndReturn(True)
        instance.ZVMInstance.is_reachable().AndReturn(True)
        instance.ZVMInstance.attach_volume(self.driver._volumeop, {},
                                           self._fake_connection_info(),
                                           self.instance, None, True)
        self.mox.ReplayAll()

        self.driver.attach_volume({}, self._fake_connection_info(),
                                  self.instance, None)
        self.mox.VerifyAll()

    def test_live_migration_same_mn(self):
        dest = 'fhost2'
        migrate_data = {'dest_host': dest,
                        'pre_live_migration_result':
                            {'same_xcat_mn': True,
                             'dest_diff_mn_key': None}}
        info = ["os000001: VMRELOCATE action=move against OS000001... Done\n"]
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self.driver.live_migration(self.context, self.instance, dest,
                                   self._fake_fun(), self._fake_fun(), None,
                                   migrate_data)
        self.mox.VerifyAll()

    def test_live_migration_diff_mn(self):
        dest = 'fhost2'
        migrate_data = {'dest_host': dest,
                        'pre_live_migration_result':
                            {'same_xcat_mn': False,
                             'dest_diff_mn_key': 'sshkey'}}
        info = ["os000001: VMRELOCATE action=move against OS000001... Done\n"]
        self.stubs.Set(zvmutils, "xdsh", self._fake_fun())
        self.stubs.Set(self.driver._networkop, 'clean_mac_switch_host',
                       self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'delete_xcat_node',
                       self._fake_fun())
        self._set_fake_xcat_resp([("PUT", None, None,
                                   self._gen_resp(info=info))])
        self.driver.live_migration(self.context, self.instance, dest,
                                   self._fake_fun(), self._fake_fun(), None,
                                   migrate_data)
        self.mox.VerifyAll()

    def test_live_migration_failed(self):
        dest = 'fhost2'
        migrate_data = {'dest_host': dest,
                        'pre_live_migration_result':
                            {'same_xcat_mn': True,
                             'dest_diff_mn_key': None}}
        info = ["os000001: VMRELOCATE MOVE error\n"]
        self._set_fake_xcat_responses([self._gen_resp(info=info,
                                                      error=['error'])])
        self.stubs.Set(zvmutils, "xdsh", self._fake_fun())
        self.assertRaises(nova_exception.MigrationError,
            self.driver.live_migration, self.context, self.instance, dest,
            self._fake_fun(), self._fake_fun(), None, migrate_data)
        self.mox.VerifyAll()

    def test_check_can_live_migrate_destination(self):
        dest = 'fhost2'
        dest_info = {'hypervisor_hostname': dest}
        res_dict = self.driver.check_can_live_migrate_destination(self.context,
            self.instance, {}, dest_info, {}, {})
        exp_dict = {'dest_host': dest,
                    'is_shared_storage': True}
        self.assertEqual(res_dict.get('migrate_data', None), exp_dict)

    def test_check_can_live_migrate_source(self):
        dest = 'fhost2'
        migrate_data = {'dest_host': dest,
                        'is_shared_storage': True}
        dest_check_data = {'migrate_data': migrate_data}
        info = ["os000001: VMRELOCATE action=test against OS000001... Done\n"]
        self._set_fake_xcat_resp([
            ("GET", None, None, self._gen_resp(info=["userid=os000001"])),
            ("PUT", None, None, self._gen_resp(info=info))])
        res_dict = self.driver.check_can_live_migrate_source(
                       self.context, self.instance, dest_check_data)
        self.assertNotEqual(res_dict.get('dest_host', None), None)
        self.mox.ReplayAll()

    def test_check_can_live_migrate_source_failed(self):
        dest = 'fhost2'
        migrate_data = {'dest_host': dest}
        dest_check_data = {'migrate_data': migrate_data}
        error = ["VMRELOCATE move failed"]

        self.mox.StubOutWithMock(zvmutils, "get_userid")
        self.mox.StubOutWithMock(self.driver, "_vmrelocate")
        zvmutils.get_userid('os000001').AndReturn("os000001")
        self.driver._vmrelocate('fhost2', 'os000001', 'test').AndRaise(
            nova_exception.MigrationError(reason=error))
        self.mox.ReplayAll()

        self.assertRaises(nova_exception.MigrationError,
            self.driver.check_can_live_migrate_source, self.context,
            self.instance, dest_check_data)
        self.mox.VerifyAll()

    def test_check_can_live_migrate_source_force(self):
        self.flags(zvm_vmrelocate_force='domain')
        dest = 'fhost2'
        migrate_data = {'dest_host': dest}
        dest_check_data = {'migrate_data': migrate_data}

        self.mox.StubOutWithMock(zvmutils, "get_userid")
        self.mox.StubOutWithMock(self.driver, "_vmrelocate")
        zvmutils.get_userid('os000001').AndReturn("os000001")
        self.driver._vmrelocate('fhost2', 'os000001', 'test').AndRaise(
            nova_exception.MigrationError(reason="1944"))
        self.mox.ReplayAll()

        self.driver.check_can_live_migrate_source(self.context, self.instance,
                                                  dest_check_data)
        self.mox.VerifyAll()

    def test_migrate_disk_and_power_off(self):

        inst = fake_instance.fake_instance_obj(self.context)

        self.stubs.Set(zvmutils, 'get_host', self._fake_fun("root@10.1.1.10"))
        self.mox.StubOutWithMock(zvmutils, 'get_userid')
        self.mox.StubOutWithMock(self.driver, '_get_eph_disk_info')
        self.mox.StubOutWithMock(self.driver, '_detach_volume_from_instance')
        self.mox.StubOutWithMock(self.driver, '_capture_disk_for_instance')
        self.mox.StubOutWithMock(self.driver, '_is_shared_image_repo')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'get_snapshot_time_path')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'get_image_from_xcat')

        zvmutils.get_userid('os000001').AndReturn('os000001')
        self.driver._get_eph_disk_info('os000001').AndReturn([])
        self.driver._detach_volume_from_instance(mox.IgnoreArg(),
                                                 mox.IgnoreArg())
        self.driver._capture_disk_for_instance({}, mox.IgnoreArg()).AndReturn(
            ('fakeimagename'))
        self.driver._is_shared_image_repo('fakeimagename').AndReturn(False)
        self.driver._zvm_images.get_snapshot_time_path().AndReturn('/tmp')
        self.driver._zvm_images.get_image_from_xcat('fakeimagename',
            'fakeimagename', '/tmp').AndReturn('/tmp/fakeimg')
        self.mox.ReplayAll()

        class FakeInstanceType:
            def __init__(self):
                self.root_gb = 10
                self.ephemeral_gb = 10

        fake_instance_type = FakeInstanceType()
        disk_info = self.driver.migrate_disk_and_power_off({}, inst,
            '10.1.1.11', fake_instance_type, [({}, {})])
        self.mox.VerifyAll()

        exp = {
            "eph_disk_info": [],
            "disk_image_name": "fakeimagename",
            "disk_owner": "os000001",
            "disk_eph_size_old": 0,
            "shared_image_repo": False,
            "disk_eph_size_new": 10,
            "disk_type": "FBA",
            "disk_source_image":
            "root@10.1.1.10:/tmp/fakeimg",
            "disk_source_mn": "10.10.10.10"
        }

        self.assertEqual(exp, jsonutils.loads(disk_info))

    def test_capture_disk_for_instance_all_in_one_mode(self):
        self.mox.StubOutWithMock(instance.ZVMInstance, 'get_provmethod')
        self.mox.StubOutWithMock(self.driver._zvm_images, 'create_zvm_image')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'update_last_use_date')
        instance.ZVMInstance.get_provmethod().AndReturn('sysclone')
        self.driver._zvm_images.create_zvm_image(self.instance, 'rszos000001',
                                mox.IgnoreArg()).AndReturn('image-name-xcat')
        self.driver._zvm_images.update_last_use_date('image-name-xcat')
        self.mox.ReplayAll()

        imgn = self.driver._capture_disk_for_instance(self.context,
                                                      self.instance)
        self.mox.VerifyAll()
        self.assertEqual(imgn, 'image-name-xcat')

    def test_migrate_disk_and_power_off_not_support(self):

        inst = fake_instance.fake_instance_obj(self.context)

        class FakeInstanceType:
            def __init__(self):
                self.root_gb = -1
                self.ephemeral_gb = 10

        fake_instance_type = FakeInstanceType()
        self.assertRaises(nova_exception.InstanceFaultRollback,
            self.driver.migrate_disk_and_power_off, self.context,
            inst, '10.1.1.11', fake_instance_type, [({}, {})])

    def test__get_eph_disk_info(self):
        self.mox.StubOutWithMock(self.driver, '_get_user_directory')
        self.driver._get_user_directory('os000001').AndReturn([
            'os000001: MDISK 0100 3390 n 100 n MR\n',
            'os000001: MDISK 0102 3390 n 200 n MR\n',
            'os000001: MDISK 0103 3390 n 300 n MR\n'])
        self.mox.ReplayAll()

        eph_disk_info = self.driver._get_eph_disk_info('os000001')
        exp = [{'device_name': '0102',
                'guest_format': None,
                'size': '200',
                'size_in_units': True,
                'vdev': '0102'},
               {'device_name': '0103',
                'guest_format': None,
                'size': '300',
                'size_in_units': True,
                'vdev': '0103'}]
        self.assertEqual(exp, eph_disk_info)

    def test__get_eph_disk_info_invalid_resp(self):
        self.mox.StubOutWithMock(self.driver, '_get_user_directory')
        self.driver._get_user_directory('os000001').AndReturn([
            'os000001: MDISK 0101\n'])
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMInvalidXCATResponseDataError,
                          self.driver._get_eph_disk_info, 'os000001')

    def test_finish_migration_same_mn(self):
        self.flags(zvm_xcat_server="10.10.10.10")
        network_info = self._fake_network_info()
        disk_info = {
            'disk_type': 'FBA',
            'disk_source_mn': '10.10.10.10',
            'disk_source_image': 'root@10.1.1.10:/fakepath/fa-ke-ima-ge.tgz',
            'disk_image_name': 'fa-ke-ima-ge',
            'disk_owner': 'os000001',
            'disk_eph_size_old': 0,
            'disk_eph_size_new': 0,
            'eph_disk_info': [],
            'shared_image_repo': False}
        migration = {'source_node': 'FAKENODE1',
                     'dest_node': 'FAKENODE2'}
        disk_info = jsonutils.dumps(disk_info)

        self.mox.StubOutWithMock(self.driver, 'get_host_ip_addr')
        self.mox.StubOutWithMock(self.driver._networkop,
                                 'clean_mac_switch_host')
        self.mox.StubOutWithMock(self.driver._pathutils, 'clean_temp_folder')
        self.mox.StubOutWithMock(self.driver, '_copy_instance')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'copy_xcat_node')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'update_node_def')
        self.mox.StubOutWithMock(self.driver, '_preset_instance_network')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_userid')
        self.mox.StubOutWithMock(self.driver, '_add_nic_to_instance')
        self.mox.StubOutWithMock(self.driver, '_deploy_root_and_ephemeral')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'delete_image_from_xcat')
        self.mox.StubOutWithMock(zvmutils, 'punch_xcat_auth_file')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'power_on')
        self.mox.StubOutWithMock(zvmutils, 'xdsh')
        self.mox.StubOutWithMock(self.driver, '_attach_volume_to_instance')

        farg = mox.IgnoreArg()
        self.driver.get_host_ip_addr().AndReturn('10.1.1.10')
        self.driver._networkop.clean_mac_switch_host(farg)
        self.driver._pathutils.clean_temp_folder(farg)
        self.driver._copy_instance(farg).AndReturn(self._fake_inst)
        instance.ZVMInstance.copy_xcat_node(farg)
        instance.ZVMInstance.update_node_def(farg, farg)
        self.driver._preset_instance_network('os000001', farg)
        instance.ZVMInstance.create_userid(farg, farg)
        self.driver._add_nic_to_instance('os000001', farg, farg)
        self.driver._deploy_root_and_ephemeral(farg, farg)
        self.driver._zvm_images.delete_image_from_xcat(farg)
        zvmutils.punch_xcat_auth_file(mox.IgnoreArg(), 'os000001')
        instance.ZVMInstance.power_on()
        self.driver._attach_volume_to_instance(farg, self._fake_inst, [])
        self.mox.ReplayAll()

        self.driver.finish_migration(self.context, migration, self._fake_inst,
                                     disk_info, network_info, None, None)
        self.mox.VerifyAll()

    def test_finish_migration_all_in_one_mode(self):
        network_info = self._fake_network_info()
        disk_info = {
            'disk_type': 'FBA',
            'disk_source_mn': '10.10.10.10',
            'disk_source_image': 'root@10.1.1.10:/fakepath/fa-ke-ima-ge.tgz',
            'disk_image_name': 'fa-ke-ima-ge',
            'disk_owner': 'os000001',
            'disk_eph_size_old': 0,
            'disk_eph_size_new': 0,
            'eph_disk_info': [],
            'shared_image_repo': True}
        migration = {'source_node': 'FAKENODE1',
                     'dest_node': 'FAKENODE2'}
        disk_info = jsonutils.dumps(disk_info)

        self.mox.StubOutWithMock(self.driver, 'get_host_ip_addr')
        self.mox.StubOutWithMock(self.driver._networkop,
                                 'clean_mac_switch_host')
        self.mox.StubOutWithMock(self.driver, '_copy_instance')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'copy_xcat_node')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'update_node_def')
        self.mox.StubOutWithMock(self.driver, '_preset_instance_network')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_userid')
        self.mox.StubOutWithMock(self.driver, '_add_nic_to_instance')
        self.mox.StubOutWithMock(self.driver, '_deploy_root_and_ephemeral')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'delete_image_from_xcat')
        self.mox.StubOutWithMock(zvmutils, 'punch_xcat_auth_file')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'power_on')
        self.mox.StubOutWithMock(zvmutils, 'xdsh')
        self.mox.StubOutWithMock(self.driver, '_attach_volume_to_instance')

        farg = mox.IgnoreArg()
        self.driver.get_host_ip_addr().AndReturn('10.1.1.10')
        self.driver._networkop.clean_mac_switch_host(farg)
        self.driver._copy_instance(farg).AndReturn(self._fake_inst)
        instance.ZVMInstance.copy_xcat_node(farg)
        instance.ZVMInstance.update_node_def(farg, farg)
        self.driver._preset_instance_network('os000001', farg)
        instance.ZVMInstance.create_userid(farg, farg)
        self.driver._add_nic_to_instance('os000001', farg, farg)
        self.driver._deploy_root_and_ephemeral(farg, farg)
        self.driver._zvm_images.delete_image_from_xcat(farg)
        zvmutils.punch_xcat_auth_file(mox.IgnoreArg(), 'os000001')
        instance.ZVMInstance.power_on()
        self.driver._attach_volume_to_instance(farg, self._fake_inst, [])
        self.mox.ReplayAll()

        self.driver.finish_migration(self.context, migration, self._fake_inst,
                                     disk_info, network_info, None, None)
        self.mox.VerifyAll()

    def test_finish_migration_same_mn_with_eph(self):
        self.flags(zvm_xcat_server="10.10.10.10")
        network_info = self._fake_network_info()
        disk_info = {
            'disk_type': 'FBA',
            'disk_source_mn': '10.10.10.10',
            'disk_source_image': 'root@10.1.1.10:/fakepath/fa-ke-ima-ge.tgz',
            'disk_image_name': 'fa-ke-ima-ge',
            'disk_owner': 'os000001',
            'disk_eph_size_old': 0,
            'disk_eph_size_new': 1,
            'eph_disk_info': [],
            'shared_image_repo': False}
        migration = {'source_node': 'FAKENODE1',
                     'dest_node': 'FAKENODE2'}
        disk_info = jsonutils.dumps(disk_info)

        self.mox.StubOutWithMock(self.driver, 'get_host_ip_addr')
        self.mox.StubOutWithMock(self.driver._networkop,
                                 'clean_mac_switch_host')
        self.mox.StubOutWithMock(self.driver._pathutils, 'clean_temp_folder')
        self.mox.StubOutWithMock(self.driver, '_copy_instance')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'copy_xcat_node')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'update_node_def')
        self.mox.StubOutWithMock(self.driver, '_preset_instance_network')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_userid')
        self.mox.StubOutWithMock(self.driver._pathutils, 'get_instance_path')
        self.mox.StubOutWithMock(zvmutils, 'process_eph_disk')
        self.mox.StubOutWithMock(self.driver, '_add_nic_to_instance')
        self.mox.StubOutWithMock(self.driver, '_deploy_root_and_ephemeral')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'delete_image_from_xcat')
        self.mox.StubOutWithMock(zvmutils, 'punch_xcat_auth_file')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'power_on')
        self.mox.StubOutWithMock(self.driver, '_attach_volume_to_instance')

        farg = mox.IgnoreArg()
        self.driver.get_host_ip_addr().AndReturn('10.1.1.10')
        self.driver._networkop.clean_mac_switch_host(farg)
        self.driver._pathutils.clean_temp_folder(farg)
        self.driver._copy_instance(farg).AndReturn(self._fake_inst)
        instance.ZVMInstance.copy_xcat_node(farg)
        instance.ZVMInstance.update_node_def(farg, farg)
        self.driver._preset_instance_network('os000001', farg)
        instance.ZVMInstance.create_userid(farg, farg)
        self.driver._pathutils.get_instance_path(farg, farg).AndReturn('/fp')
        zvmutils.process_eph_disk('os000001')
        self.driver._add_nic_to_instance('os000001', farg, farg)
        self.driver._deploy_root_and_ephemeral(farg, farg)
        self.driver._zvm_images.delete_image_from_xcat(farg)
        zvmutils.punch_xcat_auth_file(mox.IgnoreArg(), 'os000001')
        instance.ZVMInstance.power_on()
        self.driver._attach_volume_to_instance(farg, self._fake_inst, [])
        self.mox.ReplayAll()

        self.driver.finish_migration(self.context, migration, self._fake_inst,
                                     disk_info, network_info, None, None)
        self.mox.VerifyAll()

    def test_finish_migration_same_mn_deploy_failed(self):
        """Exception raised when deploying, verify networking re-configured."""
        self.flags(zvm_xcat_server="10.10.10.10")
        network_info = self._fake_network_info()
        disk_info = {
            'disk_type': 'FBA',
            'disk_source_mn': '10.10.10.10',
            'disk_source_image': 'root@10.1.1.10:/fakepath/fa-ke-ima-ge.tgz',
            'disk_image_name': 'fa-ke-ima-ge',
            'disk_owner': 'os000001',
            'disk_eph_size_old': 0,
            'disk_eph_size_new': 0,
            'eph_disk_info': [],
            'shared_image_repo': False}
        migration = {'source_node': 'FAKENODE1',
                     'dest_node': 'FAKENODE2'}
        disk_info = jsonutils.dumps(disk_info)

        self.mox.StubOutWithMock(self.driver, 'get_host_ip_addr')
        self.mox.StubOutWithMock(self.driver._networkop,
                                 'clean_mac_switch_host')
        self.mox.StubOutWithMock(self.driver._pathutils, 'clean_temp_folder')
        self.mox.StubOutWithMock(self.driver, '_copy_instance')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'copy_xcat_node')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'update_node_def')
        self.mox.StubOutWithMock(self.driver, '_preset_instance_network')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_userid')
        self.mox.StubOutWithMock(self.driver, '_add_nic_to_instance')
        self.mox.StubOutWithMock(self.driver, '_deploy_root_and_ephemeral')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'delete_image_from_xcat')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'delete_userid')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'delete_xcat_node')
        self.mox.StubOutWithMock(self.driver, '_reconfigure_networking')
        self.mox.StubOutWithMock(self.driver, '_is_nic_granted')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'power_on')

        farg = mox.IgnoreArg()
        self.driver.get_host_ip_addr().AndReturn('10.1.1.10')
        self.driver._networkop.clean_mac_switch_host(farg)
        self.driver._pathutils.clean_temp_folder(farg)
        self.driver._copy_instance(farg).AndReturn(self._fake_inst)
        instance.ZVMInstance.copy_xcat_node(farg)
        instance.ZVMInstance.update_node_def(farg, farg)
        self.driver._preset_instance_network('os000001', farg)
        instance.ZVMInstance.create_userid(farg, farg)
        self.driver._add_nic_to_instance('os000001', farg, farg)
        self.driver._deploy_root_and_ephemeral(farg, farg).AndRaise(
            exception.ZVMXCATDeployNodeFailed({'node': 'fn', 'msg': 'e'}))
        self.driver._zvm_images.delete_image_from_xcat(farg)
        instance.ZVMInstance.delete_userid('fakehcp')
        instance.ZVMInstance.delete_xcat_node()
        instance.ZVMInstance.copy_xcat_node(farg)
        instance.ZVMInstance.delete_xcat_node()
        self.driver._reconfigure_networking(farg, network_info, farg)
        self.driver._is_nic_granted(farg).AndReturn(True)
        instance.ZVMInstance.power_on()
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMXCATDeployNodeFailed,
            self.driver.finish_migration, self.context, migration,
            self._fake_inst, disk_info, network_info, None, None)
        self.mox.VerifyAll()

    def test_finish_migration_diff_mn(self):
        self.flags(zvm_xcat_server="10.10.10.11")
        network_info = self._fake_network_info()
        disk_info = {
            'disk_type': 'FBA',
            'disk_source_mn': '10.10.10.10',
            'disk_source_image': 'root@10.1.1.10:/fakepath/fa-ke-ima-ge.tgz',
            'disk_image_name': 'fa-ke-ima-ge',
            'disk_owner': 'os000001',
            'disk_eph_size_old': 0,
            'disk_eph_size_new': 0,
            'eph_disk_info': [],
            'shared_image_repo': False}
        migration = {'source_node': 'FAKENODE1',
                     'dest_node': 'FAKENODE2'}
        disk_info = jsonutils.dumps(disk_info)

        self.mox.StubOutWithMock(self.driver, 'get_host_ip_addr')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_xcat_node')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'update_node_def')
        self.mox.StubOutWithMock(self.driver._zvm_images, 'put_image_to_xcat')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'clean_up_snapshot_time_path')
        self.mox.StubOutWithMock(self.driver, '_preset_instance_network')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'create_userid')
        self.mox.StubOutWithMock(self.driver, '_add_nic_to_instance')
        self.mox.StubOutWithMock(self.driver, '_deploy_root_and_ephemeral')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'delete_image_from_xcat')
        self.mox.StubOutWithMock(zvmutils, 'punch_xcat_auth_file')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'power_on')
        self.mox.StubOutWithMock(self.driver, '_attach_volume_to_instance')

        farg = mox.IgnoreArg()
        self.driver.get_host_ip_addr().AndReturn('10.1.1.10')
        instance.ZVMInstance.create_xcat_node(farg)
        instance.ZVMInstance.update_node_def(farg, farg)
        self.driver._zvm_images.put_image_to_xcat(farg, farg)
        self.driver._zvm_images.clean_up_snapshot_time_path(farg)
        self.driver._preset_instance_network('os000001', farg)
        instance.ZVMInstance.create_userid(farg, farg)
        self.driver._add_nic_to_instance('os000001', farg, farg)
        self.driver._deploy_root_and_ephemeral(farg, farg)
        self.driver._zvm_images.delete_image_from_xcat(farg)
        zvmutils.punch_xcat_auth_file(farg, farg)
        instance.ZVMInstance.power_on()
        self.driver._attach_volume_to_instance(farg, self._fake_inst, [])
        self.mox.ReplayAll()

        self.driver.finish_migration(self.context, migration, self._fake_inst,
                                     disk_info, network_info, None, None)
        self.mox.VerifyAll()

    def test_confirm_migration_same_mn(self):
        self.flags(zvm_xcat_server="10.10.10.10")
        self.stubs.Set(self.driver, 'instance_exists', self._fake_fun(True))
        self.mox.StubOutWithMock(self.driver, 'destroy')
        self.driver.destroy({}, self._old_inst)
        self.mox.ReplayAll()
        self.driver.confirm_migration([], self._fake_inst, [])
        self.mox.VerifyAll()

    def test_confirm_migration_diff_mn(self):
        self.flags(zvm_xcat_server="10.10.10.11")
        self.stubs.Set(self.driver, 'instance_exists', self._fake_fun(False))
        self.stubs.Set(self.driver._zvm_images, 'delete_image_from_xcat',
                       self._fake_fun())
        self.stubs.Set(self.driver._zvm_images,
                       'cleanup_image_after_migration', self._fake_fun())
        self.mox.StubOutWithMock(self.driver, 'destroy')
        self.driver.destroy({}, self._fake_inst)
        self.mox.ReplayAll()
        self.driver.confirm_migration([], self._fake_inst, [])
        self.mox.VerifyAll()

    def test_finish_revert_migration_same_mn(self):
        self.flags(zvm_xcat_server="10.10.10.10")
        self.stubs.Set(instance.ZVMInstance, 'copy_xcat_node',
                       self._fake_fun())
        self.stubs.Set(instance.ZVMInstance, 'delete_xcat_node',
                       self._fake_fun())

        self.mox.StubOutWithMock(self.driver, 'instance_exists')
        self.mox.StubOutWithMock(self.driver, '_preset_instance_network')
        self.mox.StubOutWithMock(self.driver, '_add_nic_to_instance')
        self.mox.StubOutWithMock(self.driver, '_wait_for_nic_update')
        self.mox.StubOutWithMock(self.driver, '_wait_for_addnic')
        self.mox.StubOutWithMock(self.driver, '_is_nic_granted')
        self.mox.StubOutWithMock(self.driver, '_attach_volume_to_instance')
        self.mox.StubOutWithMock(self.driver, 'power_on')

        self.driver.instance_exists('rszos000001').AndReturn(True)
        self.driver._preset_instance_network('os000001',
            self._fake_network_info()).AndReturn(None)
        self.driver._add_nic_to_instance('os000001',
            self._fake_network_info(), None).AndReturn(None)
        self.driver._wait_for_nic_update('os000001').AndReturn(None)
        self.driver._wait_for_addnic('os000001').AndReturn(None)
        self.driver._is_nic_granted('os000001').AndReturn(True)
        self.driver._attach_volume_to_instance({}, mox.IgnoreArg(),
            []).AndReturn(None)
        self.driver.power_on({}, mox.IgnoreArg(), []).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.finish_revert_migration({}, self._fake_inst,
            self._fake_network_info(), None, True)
        self.mox.VerifyAll()

    def test_finish_revert_migration_diff_mn(self):
        self.flags(zvm_xcat_server="10.10.10.11")

        self.mox.StubOutWithMock(self.driver, 'instance_exists')
        self.mox.StubOutWithMock(self.driver._zvm_images,
                                 'cleanup_image_after_migration')
        self.mox.StubOutWithMock(self.driver, '_attach_volume_to_instance')

        self.driver.instance_exists('rszos000001').AndReturn(False)
        self.driver._zvm_images.cleanup_image_after_migration(
            'os000001').AndReturn(None)
        self.driver._attach_volume_to_instance({}, mox.IgnoreArg(),
            []).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.finish_revert_migration({}, self._fake_inst,
            self._fake_network_info(), None, False)

#     def test_get_host_uptime(self):
#         ipl_time = 'IPL at 03/13/14 21:43:12 EDT'
#         self.assertEqual(self.driver.get_host_uptime(None), ipl_time)

    def _fake_console_rinv_info(self):
        fake_console_rinv_info = ["fakenode: 00: zIPL boot menu\n"]
        return self._generate_xcat_resp(fake_console_rinv_info)

    # Because of Bug 534, ignore this test now
    def _test_get_console_output(self):
        self._set_fake_xcat_responses([self._fake_console_rinv_info()])
        console_log = self.driver.get_console_output(self.context,
                          self.instance).split('\n')[0]
        self.mox.VerifyAll()
        self.assertEqual('fakenode: 00: zIPL boot menu', console_log)

    def test_create_config_drive_non_tgz(self):
        self.flags(config_drive_format='nontgz')
        linuxdist = dist.ListDistManager().get_linux_dist('rhel6')
        self.assertRaises(exception.ZVMConfigDriveError,
            self.driver._create_config_drive, '', self.instance, '', '', '',
            linuxdist)

    def test_get_hcp_info(self):
        hcp_info = self.driver._get_hcp_info()
        self.assertEqual('fakehcp.fake.com', hcp_info['hostname'])
        self.assertEqual('fakehcp', hcp_info['nodename'])
        self.assertEqual('fakehcp', hcp_info['userid'])

    def test_get_hcp_info_first_call(self):
        self.driver._host_stats = []

        self.mox.StubOutWithMock(zvmutils, 'get_userid')
        zvmutils.get_userid('fakehcp').AndReturn('fakehcp')
        self.mox.ReplayAll()

        hcp_info = self.driver._get_hcp_info('fakehcp.fake.com')
        self.mox.VerifyAll()
        self.assertEqual('fakehcp.fake.com', hcp_info['hostname'])
        self.assertEqual('fakehcp', hcp_info['nodename'])
        self.assertEqual('fakehcp', hcp_info['userid'])

    def test_get_hcp_info_incorrect_init(self):
        self.driver._host_stats = []
        fake_hcpinfo = {'hostname': 'fakehcp.fake.com',
                        'nodename': 'fakehcp',
                        'userid': 'fakehcp'}

        self.mox.StubOutWithMock(self.driver, 'update_host_status')
        self.driver.update_host_status().AndReturn([{'zhcp': fake_hcpinfo}])
        self.mox.ReplayAll()

        hcp_info = self.driver._get_hcp_info()
        self.mox.VerifyAll()
        self.assertEqual('fakehcp.fake.com', hcp_info['hostname'])
        self.assertEqual('fakehcp', hcp_info['nodename'])
        self.assertEqual('fakehcp', hcp_info['userid'])

    def test_detach_volume_from_instance(self):
        bdm = [{'connection_info': 'fake', 'mount_device': None}]
        self.mox.StubOutWithMock(self.driver, 'instance_exists')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'is_reachable')
        self.mox.StubOutWithMock(instance.ZVMInstance, 'detach_volume')
        self.driver.instance_exists('os000001').AndReturn(True)
        instance.ZVMInstance.is_reachable().AndReturn(True)
        instance.ZVMInstance.detach_volume(self.driver._volumeop, 'fake',
            mox.IgnoreArg(), None, True, rollback=False)
        self.mox.ReplayAll()

        self.driver._detach_volume_from_instance(self.instance, bdm)
        self.mox.VerifyAll()

    def test_is_shared_image_repo_not_exist(self):
        self.mox.StubOutWithMock(self.driver, '_get_xcat_image_file_path')
        self.driver._get_xcat_image_file_path('img-uuid').AndRaise(
                                        exception.ZVMImageError(msg='err'))
        self.mox.ReplayAll()

        self.assertFalse(self.driver._is_shared_image_repo('img-uuid'))
        self.mox.VerifyAll()

    def test_get_available_nodes(self):
        nodes = self.driver.get_available_nodes()
        self.assertEqual(nodes[0], 'fakenode')


class ZVMInstanceTestCases(ZVMTestCase):
    """Test cases for zvm.instance."""

    _fake_inst_info_list = [
        "os000001: Uptime: 4 days 20 hr 00 min",
        "os000001: CPU Used Time: 330528353",
        "os000001: Total Memory: 128M",
        "os000001: Max Memory: 2G",
        "os000001: ",
        "os000001: Processors: ",
        "os000001:     CPU 03  ID  FF00EBBE20978000 CP  CPUAFF ON",
        "os000001:     CPU 00  ID  FF00EBBE20978000 (BASE) CP  CPUAFF ON",
        "os000001:     CPU 01  ID  FF00EBBE20978000 CP  CPUAFF ON",
        "os000001:     CPU 02  ID  FF00EBBE20978000 CP  CPUAFF ON",
        "os000001: ",
    ]

    def setUp(self):
        super(ZVMInstanceTestCases, self).setUp()
        self.flags(zvm_user_profile='fakeprof')
        self.instance['ephemeral_gb'] = 0
        self._instance = instance.ZVMInstance(self.instance)

    def test_create_xcat_node(self):
        info = ["1 object definitions have been created or modified."]
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self._instance.create_xcat_node('fakehcp')
        self.mox.VerifyAll()

    def test_create_xcat_node_failed(self):
        resp = {'data': [{'errorcode': ['1'],
                          'error': ["One or more errors occured\n"]}]}
        self._set_fake_xcat_responses([resp])
        self.assertRaises(exception.ZVMXCATCreateNodeFailed,
                          self._instance.create_xcat_node, 'fakehcp')
        self.mox.VerifyAll()

    def test_add_mdisk_eckd(self):
        info = ["os000001: Adding a disk to LINUX171... Done\n"
                "os000001: Active disk configuration... Done\n"]
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self._instance.add_mdisk('fakedp', '0101', '1g')

    def test_add_mdisk_fba_with_fmt(self):
        self.flags(zvm_diskpool_type='FBA')
        info = ["os000001: Adding a disk to LINUX171... Done\n"
                "os000001: Active disk configuration... Done\n"]
        punch_body = ["--add9336 fakedp 0101 1g MR '' '' '' ext3"]
        self._set_fake_xcat_resp([
            ("PUT", None, punch_body, self._gen_resp(info=info))
            ])
        self._instance.add_mdisk('fakedp', '0101', '1g', 'ext3')
        self.mox.VerifyAll()

    def test_set_ipl(self):
        info = ["os000001: Adding IPL statement to OS000001's "
                "directory entry... Done\n"]
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self._instance._set_ipl('0100')

    def test_create_userid(self):
        """Create userid."""
        info = ['os000001: Defining OS000001 in directory... Done\n'
                'os000001: Granting VSwitch for OS000001... Done\n']
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self.stubs.Set(self._instance, '_set_ipl', lambda *args: None)
        self.stubs.Set(self._instance, 'add_mdisk', lambda *args: None)
        self._instance.create_userid({}, {'min_disk': 3})

    def test_create_userid_has_ephemeral(self):
        """Create userid with epheral disk added."""
        self._instance._instance['ephemeral_gb'] = 20
        cu_info = ['os000001: Defining OS000001 in directory... Done\n'
                   'os000001: Granting VSwitch for OS000001... Done\n']
        am_info = ["os000001: Adding a disk to OS00001... Done\n"
                   "os000001: Active disk configuration... Done\n"]
        self._set_fake_xcat_responses([self._generate_xcat_resp(cu_info),
                                       self._generate_xcat_resp(am_info),
                                       self._generate_xcat_resp(am_info)])
        self.stubs.Set(self._instance, '_set_ipl', lambda *args: None)
        self._instance.create_userid({}, {'min_disk': 3})
        self.mox.VerifyAll()

    def test_create_userid_with_eph_opts(self):
        """Create userid with '--ephemeral' options."""
        self._instance._instance['ephemeral_gb'] = 20
        fake_bdi = {'ephemerals': [
            {'device_name': '/dev/sdb',
             'device_type': None,
             'disk_bus': None,
             'guest_format': u'ext4',
             'size': 2},
            {'device_name': '/dev/sdc',
             'device_type': None,
             'disk_bus': None,
             'guest_format': u'ext3',
             'size': 1}]}

        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        self.mox.StubOutWithMock(self._instance, 'add_mdisk')
        self.mox.StubOutWithMock(self._instance, '_set_ipl')

        zvmutils.xcat_request('POST', mox.IgnoreArg(), mox.IgnoreArg())
        self._instance.add_mdisk('fakedp', '0100', '10g')
        self._instance._set_ipl('0100')
        self._instance.add_mdisk('fakedp', '0102', '2g', 'ext4')
        self._instance.add_mdisk('fakedp', '0103', '1g', 'ext3')
        self.mox.ReplayAll()

        self._instance.create_userid(fake_bdi, {})
        self.mox.VerifyAll()

    def test_create_userid_with_eph_opts_resize(self):
        """Create userid with '--ephemeral' options."""
        self._instance._instance['ephemeral_gb'] = 20
        fake_bdi = {'ephemerals': [
            {'device_name': '/dev/sdb',
             'device_type': None,
             'disk_bus': None,
             'guest_format': u'ext4',
             'size': '200000',
             'size_in_units': True},
            {'device_name': '/dev/sdc',
             'device_type': None,
             'disk_bus': None,
             'guest_format': u'ext3',
             'size': '100000',
             'size_in_units': True}]}

        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        self.mox.StubOutWithMock(self._instance, 'add_mdisk')
        self.mox.StubOutWithMock(self._instance, '_set_ipl')

        zvmutils.xcat_request('POST', mox.IgnoreArg(), mox.IgnoreArg())
        self._instance.add_mdisk('fakedp', '0100', '10g')
        self._instance._set_ipl('0100')
        self._instance.add_mdisk('fakedp', '0102', '200000', 'ext4')
        self._instance.add_mdisk('fakedp', '0103', '100000', 'ext3')
        self.mox.ReplayAll()

        self._instance.create_userid(fake_bdi, {})
        self.mox.VerifyAll()

    def test_update_node_info(self):
        image_meta = {'name': 'fake',
                      'id': '00-11-22-33',
                      'properties': {'os_version': 'fake',
                                     'architecture': 'fake',
                                     'provisioning_method': 'fake'}}
        info = ['os000001: update node info ... Done\n']
        self._set_fake_xcat_responses([self._generate_xcat_resp(info)])
        self._instance.update_node_info(image_meta)

    @mock.patch('nova.virt.zvm.utils.xcat_request')
    @mock.patch('nova.virt.zvm.utils.get_host')
    def test_deploy_node(self, get_host, xcat_req):
        get_host.return_value = 'fake@fakehost'
        xcat_req.return_value = 'fake'
        self._instance.deploy_node('fakeimg', '/fake/file', '0100')

    @mock.patch('nova.virt.zvm.utils.xcat_request')
    @mock.patch('nova.virt.zvm.utils.get_host')
    def test_deploy_node_failed(self, get_host, xcat_req):
        get_host.return_value = 'fake@fakehost'
        xcat_req.side_effect = exception.ZVMXCATDeployNodeFailed(node="fake",
                                                                 msg='msg')
        self.assertRaises(exception.ZVMXCATDeployNodeFailed,
                          self._instance.deploy_node, 'fakeimg', '/fake/file')

    def test_delete_userid(self):
        resp = {'error': [['Return Code: 400\nReason Code: 16\n']]}

        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        self.mox.StubOutWithMock(self._instance, '_wait_for_unlock')
        zvmutils.xcat_request("DELETE", mox.IgnoreArg()).AndRaise(
            exception.ZVMXCATInternalError(msg=str(resp)))
        self._instance._wait_for_unlock('fakehcp')
        zvmutils.xcat_request("DELETE", mox.IgnoreArg())
        self.mox.ReplayAll()

        self._instance.delete_userid('fakehcp')
        self.mox.VerifyAll()

    def test_delete_userid_400012(self):
        resp = {'error': [['Return Code: 400\nReason Code: 12\n']]}

        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        self.mox.StubOutWithMock(self._instance, '_wait_for_unlock')
        zvmutils.xcat_request("DELETE", mox.IgnoreArg()).AndRaise(
            exception.ZVMXCATInternalError(msg=str(resp)))
        self._instance._wait_for_unlock('fakehcp')
        zvmutils.xcat_request("DELETE", mox.IgnoreArg())
        self.mox.ReplayAll()

        self._instance.delete_userid('fakehcp')
        self.mox.VerifyAll()

    def test_is_locked_true(self):
        resp = {'data': [['os000001: os000001 is locked']]}

        self.mox.StubOutWithMock(zvmutils, 'xdsh')
        zvmutils.xdsh('fakehcp',
                    "smcli Image_Lock_Query_DM -T os000001").AndReturn(resp)
        self.mox.ReplayAll()

        locked = self._instance.is_locked('fakehcp')
        self.mox.VerifyAll()

        self.assertTrue(locked)

    def test_is_locked_false(self):
        resp = {'data': [['os000001: os000001 is Unlocked...']]}

        self.mox.StubOutWithMock(zvmutils, 'xdsh')
        zvmutils.xdsh('fakehcp',
                    "smcli Image_Lock_Query_DM -T os000001").AndReturn(resp)
        self.mox.ReplayAll()

        locked = self._instance.is_locked('fakehcp')
        self.mox.VerifyAll()

        self.assertFalse(locked)

    def test_wait_for_unlock(self):
        self.mox.StubOutWithMock(self._instance, 'is_locked')
        self._instance.is_locked('fakehcp').AndReturn(True)
        self._instance.is_locked('fakehcp').AndReturn(False)
        self.mox.ReplayAll()

        self._instance._wait_for_unlock('fakehcp', 1)
        self.mox.VerifyAll()

    def test_modify_storage_format(self):
        mem = self._instance._modify_storage_format('0')
        self.assertEqual(0, mem)

    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_rinv_info')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance.is_reachable')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_power_stat')
    def test_get_info(self, mk_get_ps, mk_is_reach, mk_get_rinv_info):
        mk_get_ps.return_value = power_state.RUNNING
        mk_is_reach.return_value = True
        mk_get_rinv_info.return_value = self._fake_inst_info_list

        inst_info = self._instance.get_info()
        self.assertEqual(0x01, inst_info.state)
        self.assertEqual(131072, inst_info.mem_kb)
        self.assertEqual(4, inst_info.num_cpu)
        self.assertEqual(330528353, inst_info.cpu_time_ns)
        self.assertEqual(1048576, inst_info.max_mem_kb)

    @mock.patch('nova.virt.zvm.exception.ZVMXCATInternalError.msg_fmt')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_rinv_info')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance.is_reachable')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_power_stat')
    def test_get_info_inv_err(self, mk_get_ps, mk_is_reach, mk_get_rinv_info,
                              mk_msg_fmt):
        mk_get_ps.return_value = power_state.RUNNING
        mk_is_reach.return_value = True
        mk_get_rinv_info.side_effect = exception.ZVMXCATInternalError
        mk_msg_fmt.return_value = "fake msg"

        self.assertRaises(nova_exception.InstanceNotFound,
                          self._instance.get_info)

    @mock.patch('nova.virt.zvm.exception.ZVMInvalidXCATResponseDataError.'
                'msg_fmt')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_current_memory')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_rinv_info')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance.is_reachable')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_power_stat')
    def test_get_info_invalid_data(self, mk_get_ps, mk_is_reach,
                                   mk_get_rinv_info, mk_get_mem, mk_msg_fmt):
        mk_get_ps.return_value = power_state.RUNNING
        mk_is_reach.return_value = True
        mk_get_rinv_info.return_value = self._fake_inst_info_list
        mk_get_mem.side_effect = exception.ZVMInvalidXCATResponseDataError
        mk_msg_fmt.return_value = "fake msg"

        inst_info = self._instance.get_info()
        self.assertEqual(0x01, inst_info.state)
        self.assertEqual(1048576, inst_info.mem_kb)
        self.assertEqual(2, inst_info.num_cpu)
        self.assertEqual(0, inst_info.cpu_time_ns)
        self.assertEqual(1048576, inst_info.max_mem_kb)

    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_rinv_info')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance.is_reachable')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_power_stat')
    def test_get_info_down(self, mk_get_ps, mk_is_reach, mk_get_rinv_info):
        mk_get_ps.return_value = power_state.SHUTDOWN
        mk_is_reach.return_value = False
        mk_get_rinv_info.return_value = self._fake_inst_info_list

        inst_info = self._instance.get_info()
        self.assertEqual(power_state.SHUTDOWN, inst_info.state)
        self.assertEqual(0, inst_info.mem_kb)
        self.assertEqual(2, inst_info.num_cpu)
        self.assertEqual(0, inst_info.cpu_time_ns)
        self.assertEqual(1048576, inst_info.max_mem_kb)

    @mock.patch('nova.virt.zvm.instance.ZVMInstance.is_reachable')
    @mock.patch('nova.virt.zvm.instance.ZVMInstance._get_power_stat')
    def test_get_info_paused(self, mk_get_ps, mk_is_reach):
        mk_get_ps.return_value = power_state.RUNNING
        mk_is_reach.return_value = False

        _inst = fake_instance.fake_instance_obj(self.context, name='fake',
                    power_state=power_state.PAUSED, memory_mb='1024',
                    vcpus='2')
        inst = instance.ZVMInstance(_inst)

        inst_info = inst.get_info()
        self.assertEqual(power_state.PAUSED, inst_info.state)
        self.assertEqual(1048576, inst_info.mem_kb)
        self.assertEqual(2, inst_info.num_cpu)
        self.assertEqual(0, inst_info.cpu_time_ns)
        self.assertEqual(1048576, inst_info.max_mem_kb)


class ZVMXCATConnectionTestCases(test.TestCase):
    """Test cases for xCAT connection."""

    def setUp(self):
        super(ZVMXCATConnectionTestCases, self).setUp()
        self.flags(zvm_xcat_server='10.10.10.10',
                   zvm_xcat_username='fake',
                   zvm_xcat_password='fake')

    def _set_fake_response(self, response):
        self.mox.StubOutWithMock(httplib.HTTPSConnection, 'request')
        httplib.HTTPSConnection.request(mox.IgnoreArg(), mox.IgnoreArg(),
                                        mox.IgnoreArg(), mox.IgnoreArg())
        self.mox.StubOutWithMock(httplib.HTTPSConnection, 'getresponse')
        httplib.HTTPSConnection.getresponse().AndReturn(response)
        self.mox.ReplayAll()

    def test_get(self):
        self._set_fake_response(FakeHTTPResponse(200, 'OK', 'fake'))
        conn = zvmutils.XCATConnection()
        conn.request("GET", 'fakeurl')
        self.mox.VerifyAll()

    def test_get_failed(self):
        self._set_fake_response(FakeHTTPResponse(201, 'OK', 'fake'))
        conn = zvmutils.XCATConnection()
        self.assertRaises(exception.ZVMXCATRequestFailed, conn.request,
                          "GET", 'fakeurl')
        self.mox.VerifyAll()

    def test_post(self):
        self._set_fake_response(FakeHTTPResponse(201, 'Created', 'fake'))
        conn = zvmutils.XCATConnection()
        conn.request("POST", 'fakeurl')
        self.mox.VerifyAll()

    def test_post_failed(self):
        self._set_fake_response(FakeHTTPResponse(200, 'OK', 'fake'))
        conn = zvmutils.XCATConnection()
        self.assertRaises(exception.ZVMXCATRequestFailed, conn.request,
                          "POST", 'fakeurl')
        self.mox.VerifyAll()

    def test_invalid_url(self):
        self.mox.StubOutWithMock(httplib.HTTPSConnection, 'request')
        httplib.HTTPSConnection.request(mox.IgnoreArg(), mox.IgnoreArg(),
            mox.IgnoreArg(), mox.IgnoreArg()).AndRaise(socket.gaierror())
        self.mox.ReplayAll()
        conn = zvmutils.XCATConnection()
        self.assertRaises(exception.ZVMXCATRequestFailed,
                          conn.request, "GET", 'fakeurl')


class ZVMNetworkTestCases(ZVMTestCase):
    """Test cases for network operator."""

    def setUp(self):
        super(ZVMNetworkTestCases, self).setUp()
        self.networkop = networkop.NetworkOperator()
        self.iname = self.instance['name']

    def test_config_xcat_mac(self):
        self._set_fake_xcat_responses([{'data': [{'data': ['mac']}]}])
        self.networkop.config_xcat_mac(self.iname)

    def test_add_xcat_host(self):
        self._set_fake_xcat_responses([{'data': [{'data': ['mac']}]}])
        self.networkop.add_xcat_host(self.iname, '11.11.11.11', self.iname)

    def test_makehosts(self):
        self._set_fake_xcat_responses([{'data': [{'data': ['mac']}]}])
        self.networkop.makehosts()

    def test_add_instance_nic(self):
        self._set_fake_xcat_responses([{'data': [{'data': ['Done']}]}])
        self.networkop._add_instance_nic('fakehcp', self.iname, '1000', 'fake')
        self.mox.VerifyAll()

    def test_add_instance_err(self):
        self._set_fake_xcat_responses([{'data': [{'error': ['Error: err']}]}])
        self.assertRaises(exception.ZVMNetworkError,
                          self.networkop._add_instance_nic, 'fakehcp',
                          self.iname, '1000', 'fake')
        self.mox.VerifyAll()

    def test_add_instance_err_and_warn(self):
        self._set_fake_xcat_responses(
            [{'data': [
                {'error': ['Error: err']},
                {'error': ['Warning: Permanently added zhcp host']}
                ]}])
        self.assertRaises(exception.ZVMNetworkError,
                          self.networkop._add_instance_nic, 'fakehcp',
                          self.iname, '1000', 'fake')
        self.mox.VerifyAll()

    def test_add_instance_ignore_warn(self):
        self._set_fake_xcat_responses(
            [{'data': [
                {'data': [{'error': ['Warning: Permanently added zhcp host']}]}
                ]}])
        self.networkop._add_instance_nic('fakehcp', self.iname, '1000', 'fake')
        self.mox.VerifyAll()

    def test_add_instance_ignore_recoverable_issue(self):
        data = 'Return Code: 596\n Reason Code: 1186'
        self.assertFalse(zvmutils._is_recoverable_issue(data))

        data = 'Return Code: 597\n Reason Code: 1185'
        self.assertFalse(zvmutils._is_recoverable_issue(data))

        data = 'Return Code: 597\n Reason Code: 1186'
        self.assertFalse(zvmutils._is_recoverable_issue(data))

        data = 'Return Code: 596\n Reason Code: 1185'
        self.assertTrue(zvmutils._is_recoverable_issue(data))


class ZVMUtilsTestCases(ZVMTestCase):

    def setUp(self):
        super(ZVMUtilsTestCases, self).setUp()

    def test_generate_eph_vdev(self):
        vdev0 = zvmutils.generate_eph_vdev(0)
        vdev1 = zvmutils.generate_eph_vdev(1)
        vdev2 = zvmutils.generate_eph_vdev(253)
        self.assertEqual(vdev0, '0102')
        self.assertEqual(vdev1, '0103')
        self.assertEqual(vdev2, '01ff')
        self.assertRaises(exception.ZVMDriverError,
                          zvmutils.generate_eph_vdev, -1)
        self.assertRaises(exception.ZVMDriverError,
                          zvmutils.generate_eph_vdev, 254)

    def test__log_warnings(self):
        resp = {'info': [''],
                'data': [],
                'node': ['Warn ...'],
                'error': []}
        self.mox.StubOutWithMock(zvmutils.LOG, 'info')
        msg = _("Warning from xCAT: %s")
        zvmutils.LOG.info(msg % str(resp['node']))
        self.mox.ReplayAll()

        zvmutils._log_warnings(resp)
        self.mox.VerifyAll()

    def test_parse_os_version(self):
        fake_os = {'rhel': ['rhelx.y', 'redhatx.y', 'red hatx.y'],
                   'sles': ['susex.y', 'slesx.y']}
        for distro, patterns in fake_os.items():
            for i in patterns:
                os, version = zvmutils.parse_os_version(i)
                self.assertEqual(os, distro)
                self.assertEqual(version, 'x.y')

    def test_parse_os_version_exception(self):
        self.assertRaises(exception.ZVMImageError,
                          zvmutils.parse_os_version,
                          'ubuntu')

    def test_xcat_cmd_gettab(self):
        fake_resp = {"data": [["/install"]]}
        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        zvmutils.xcat_request("GET", mox.IgnoreArg()).AndReturn(fake_resp)
        self.mox.ReplayAll()

        outp = zvmutils.xcat_cmd_gettab("site", "key", "installdir", "value")
        self.mox.VerifyAll()
        self.assertEqual(outp, "/install")

    def test_xcat_cmd_gettab_multi_attr(self):
        attr_list = ['name', 'type', 'version']
        res_data = {'data': [['table.name: fake'],
                             ['table.type: fake'],
                             ['table.version: fake']]}

        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        zvmutils.xcat_request('GET', mox.IgnoreArg()).AndReturn(res_data)
        self.mox.ReplayAll()

        outp = zvmutils.xcat_cmd_gettab_multi_attr('table', 'id', 'fake',
                                                   attr_list)
        self.mox.VerifyAll()
        self.assertEqual(outp['name'], 'fake')
        self.assertEqual(outp['type'], 'fake')
        self.assertEqual(outp['version'], 'fake')

    def test_generate_network_configration(self):
        network = model.Network(bridge=None, subnets=[model.Subnet(
                  ips=[model.FixedIP(meta={}, version=4, type=u'fixed',
                  floating_ips=[], address=u'192.168.11.12')],
                  version=4, meta={'dhcp_server': u'192.168.11.7'},
                  dns=[], routes=[], cidr=u'192.168.11.0/24',
                  gateway=model.IP(address=None, type='gateway'))], meta={},
                  id=u'51e1f38b-f717-4a8b-9fb4-acefc2d7224e',
                  label=u'mgt')
        vdev = '1000'
        device_num = 0
        os_type = 'rhel'
        (cfg_str, cmd_str, dns_str,
            route_str) = zvmutils.NetworkUtils().generate_network_configration(
                              network, vdev, device_num, os_type)

        self.assertEqual(cfg_str, 'DEVICE="eth0"\nBOOTPROTO="static"\n'
                         'BROADCAST="192.168.11.255"\nGATEWAY=""\n'
                         'IPADDR="192.168.11.12"\nNETMASK="255.255.255.0"\n'
                         'NETTYPE="qeth"\nONBOOT="yes"\nPORTNAME="PORT1000"\n'
                         'OPTIONS="layer2=1"\nSUBCHANNELS='
                         '"0.0.1000,0.0.1001,0.0.1002"\n')
        self.assertEqual(cmd_str, None)
        self.assertEqual(dns_str, '')
        self.assertEqual(route_str, '')


class ZVMConfigDriveTestCase(test.NoDBTestCase):

    def setUp(self):
        super(ZVMConfigDriveTestCase, self).setUp()
        self.flags(config_drive_format='tgz',
                   tempdir='/tmp/os')
        self.inst_md = FakeInstMeta()

    def test_create_configdrive_tgz(self):
        self._file_path = CONF.tempdir
        fileutils.ensure_tree(self._file_path)
        self._file_name = self._file_path + '/configdrive.tgz'

        try:
            with configdrive.ZVMConfigDriveBuilder(
                                            instance_md=self.inst_md) as c:
                c.make_drive(self._file_name)

            self.assertTrue(os.path.exists(self._file_name))

        finally:
            fileutils.remove_path_on_error(self._file_path)

    def test_make_tgz(self):
        self._file_path = CONF.tempdir
        fileutils.ensure_tree(self._file_path)
        self._file_name = self._file_path + '/configdrive.tgz'

        self.mox.StubOutWithMock(os, 'getcwd')
        os.getcwd().AndRaise(OSError('msg'))
        os.getcwd().AndReturn(self._file_path)
        os.getcwd().AndReturn(self._file_path)
        self.mox.ReplayAll()

        try:
            with configdrive.ZVMConfigDriveBuilder(
                                            instance_md=self.inst_md) as c:
                c.make_drive(self._file_name)
        finally:
            fileutils.remove_path_on_error(self._file_path)

        self.mox.VerifyAll()


class ZVMVolumeOperatorTestCase(ZVMTestCase):

    def setUp(self):
        super(ZVMVolumeOperatorTestCase, self).setUp()
        self.volumeop = volumeop.VolumeOperator()
        self.mox.UnsetStubs()

    def test_init(self):
        self.assertIsInstance(self.volumeop._svc_driver, volumeop.SVCDriver)

    def test_attach_volume_to_instance_check_args(self):
        fake_connection_info = {'info': 'fake_info'}
        fake_instance = {'name': 'fake_instance'}
        fake_is_active = True

        farg = mox.IgnoreArg()
        self.assertRaises(exception.ZVMDriverError,
                          self.volumeop.attach_volume_to_instance, None,
                          fake_connection_info, fake_instance, farg, None)
        self.assertRaises(exception.ZVMDriverError,
                          self.volumeop.attach_volume_to_instance, None,
                          fake_connection_info, None, farg, fake_is_active)
        self.assertRaises(exception.ZVMDriverError,
                          self.volumeop.attach_volume_to_instance, None,
                          None, fake_instance, farg, fake_is_active)

    def test_attach_volume_to_instance_active(self):
        fake_connection_info = {'info': 'fake_info'}
        fake_instance = {'name': 'fake_instance'}
        is_active = True

        farg = mox.IgnoreArg()
        self.mox.StubOutWithMock(self.volumeop._svc_driver,
                                 'attach_volume_active')
        self.volumeop._svc_driver.attach_volume_active(
                farg, farg, farg, farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.volumeop.attach_volume_to_instance(farg, fake_connection_info,
                                                fake_instance, farg, is_active)
        self.mox.VerifyAll()

    def test_attach_volume_to_instance_inactive(self):
        fake_connection_info = {'info': 'fake_info'}
        fake_instance = {'name': 'fake_instance'}
        is_active = False

        farg = mox.IgnoreArg()
        self.mox.StubOutWithMock(self.volumeop._svc_driver,
                                 'attach_volume_inactive')
        self.volumeop._svc_driver.attach_volume_inactive(
                farg, farg, farg, farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.volumeop.attach_volume_to_instance(farg, fake_connection_info,
                                                fake_instance, farg, is_active)
        self.mox.VerifyAll()

    def test_detach_volume_from_instance_active(self):
        fake_connection_info = {'info': 'fake_info'}
        fake_instance = {'name': 'fake_instance'}
        is_active = True

        farg = mox.IgnoreArg()
        self.mox.StubOutWithMock(self.volumeop._svc_driver,
                                 'detach_volume_active')
        self.volumeop._svc_driver.detach_volume_active(farg, farg, farg,
                                                       farg).AndReturn(None)
        self.mox.ReplayAll()

        self.volumeop.detach_volume_from_instance(fake_connection_info,
                                                  fake_instance, farg,
                                                  is_active)
        self.mox.VerifyAll()

    def test_detach_volume_from_instance_inactive(self):
        fake_connection_info = {'info': 'fake_info'}
        fake_instance = {'name': 'fake_instance'}
        is_active = False

        farg = mox.IgnoreArg()
        self.mox.StubOutWithMock(self.volumeop._svc_driver,
                                 'detach_volume_inactive')
        self.volumeop._svc_driver.detach_volume_inactive(farg, farg, farg,
                                                         farg).AndReturn(None)
        self.mox.ReplayAll()

        self.volumeop.detach_volume_from_instance(fake_connection_info,
                                                  fake_instance, farg,
                                                  is_active)
        self.mox.VerifyAll()

    def test_get_volume_connector_check_args(self):
        self.assertRaises(exception.ZVMDriverError,
                          self.volumeop.get_volume_connector, None)


class FCPTestCase(ZVMTestCase):

    def setUp(self):
        super(FCPTestCase, self).setUp()
        fcp_info = ['opnstk1: FCP device number: B83D',
                    'opnstk1:   Status: Free',
                    'opnstk1:   NPIV world wide port number: NONE',
                    'opnstk1:   Channel path ID: 5A',
                    'opnstk1:   Physical world wide port number: '
                                '20076D8500005181']
        self.fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.mox.UnsetStubs()

    def test_init(self):
        self.assertEqual('b83d', self.fcp.get_dev_no())
        self.assertEqual(None, self.fcp.get_npiv_port())
        self.assertEqual('5A', self.fcp.get_chpid())
        self.assertEqual('20076d8500005181', self.fcp.get_physical_port())
        self.assertTrue(self.fcp.is_valid())

    def test_get_wwpn_from_line(self):
        info_line = 'opnstk1:   NPIV world wide port number: NONE'
        self.assertIsNone(self.fcp._get_wwpn_from_line(info_line))
        info_line = 'opnstk1:   NPIV world wide port number: 20076D8500005181'
        self.assertEqual('20076d8500005181',
                         self.fcp._get_wwpn_from_line(info_line))
        info_line = 'opnstk1:   Physical world wide port number:'
        self.assertIsNone(self.fcp._get_wwpn_from_line(info_line))
        info_line = ' '.join(['opnstk1:   Physical world wide port number:',
                              '20076D8500005182'])
        self.assertEqual('20076d8500005182',
                         self.fcp._get_wwpn_from_line(info_line))

    def test_get_dev_number_from_line(self):
        info_line = 'opnstk1: FCP device number: B83D'
        self.assertEqual('b83d', self.fcp._get_dev_number_from_line(info_line))
        info_line = 'opnstk1: FCP device number: '
        self.assertIsNone(self.fcp._get_dev_number_from_line(info_line))

    def test_get_chpid_from_line(self):
        info_line = 'opnstk1:   Channel path ID: 5A'
        self.assertEqual('5A', self.fcp._get_chpid_from_line(info_line))
        info_line = 'opnstk1:   Channel path ID: '
        self.assertIsNone(self.fcp._get_chpid_from_line(info_line))

    def test_validate_device(self):
        dev_line = 'opnstk1: FCP device number: B83D'
        status_line = 'opnstk1:   Status: Free'
        npiv_line = 'opnstk1:   NPIV world wide port number: NONE'
        chpid_line = 'opnstk1:   Channel path ID: 5A'
        physical_port_line = ' '.join(['opnstk1:',
                                       'Physical world wide port number:',
                                       '20076D8500005181'])

        fcp_info = ['opnstk1: FCP device number: ', status_line, npiv_line,
                    chpid_line, physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = ['opnstk1: FCP device number: help', status_line, npiv_line,
                    chpid_line, physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, '', npiv_line, chpid_line, physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertTrue(fcp.is_valid())

        fcp_info = [dev_line, status_line,
                    'opnstk1:   NPIV world wide port number: ',
                    chpid_line, physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertTrue(fcp.is_valid())

        fcp_info = [dev_line, status_line,
                    'opnstk1:   NPIV world wide port number: 20076D850000',
                    chpid_line, physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line,
                    'opnstk1:   NPIV world wide port number: 20076D850000help',
                    chpid_line, physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line,
                    'opnstk1:   NPIV world wide port number: 20076D8500005182',
                    chpid_line, physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertTrue(fcp.is_valid())

        fcp_info = [dev_line, status_line, npiv_line,
                    'opnstk1:   Channel path ID: ', physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line, npiv_line,
                    'opnstk1:   Channel path ID: help', physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line, npiv_line,
                    'opnstk1:   Channel path ID: 5', physical_port_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line, npiv_line, npiv_line,
                    'opnstk1:   Physical world wide port number: ']
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line, npiv_line, npiv_line,
                    'opnstk1:   Physical world wide port number: 20076D850000']
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line, npiv_line, npiv_line,
                    'opnstk1:   Physical world wide port number: '
                    '20076D850000help']
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())

        fcp_info = [dev_line, status_line, npiv_line, npiv_line]
        fcp = volumeop.SVCDriver.FCP(fcp_info)
        self.assertFalse(fcp.is_valid())


class SVCDriverTestCase(ZVMTestCase):

    def setUp(self):
        super(SVCDriverTestCase, self).setUp()
        self.driver = volumeop.SVCDriver()
        self.mox.UnsetStubs()

    def test_init(self):
        self.assertIsInstance(self.driver._xcat_url, zvmutils.XCATUrl)
        self.assertIsInstance(self.driver._path_utils, zvmutils.PathUtils)

    def test_init_host_check_args(self):
        self.assertRaises(exception.ZVMDriverError,
                          self.driver.init_host, None)

    def test_init_host_no_fcp(self):
        fake_host_stats = [{'zhcp': {'nodename': 'fakename'}}]
        self.flags(zvm_fcp_list=None)

        self.mox.StubOutWithMock(self.driver, '_expand_fcp_list')
        self.driver._expand_fcp_list(mox.IgnoreArg()).AndReturn(set())
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.init_host, fake_host_stats)
        self.mox.VerifyAll()

    def test_init_host(self):
        fake_host_stats = [{'zhcp': {'nodename': 'fakename'}}]
        self.mox.StubOutWithMock(self.driver, '_expand_fcp_list')
        self.mox.StubOutWithMock(self.driver, '_attach_device')
        self.mox.StubOutWithMock(self.driver, '_online_device')
        self.mox.StubOutWithMock(self.driver, '_init_fcp_pool')

        farg = mox.IgnoreArg()
        self.driver._expand_fcp_list(farg).AndReturn(['1FB0'])
        self.driver._attach_device(farg, farg).AndReturn(None)
        self.driver._online_device(farg, farg).AndReturn(None)
        self.driver._init_fcp_pool(farg).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.init_host(fake_host_stats)
        self.mox.VerifyAll()

    def test_init_fcp_pool(self):
        fake_fcp_list = '0001-0003'
        fake_instance = {'name': 'fake'}
        fake_bdm = {'instance': fake_instance, 'instance_bdms': ['fake_bdm']}
        fake_connection_info = {'data': {'zvm_fcp': '0002'}}
        farg = mox.IgnoreArg()

        self.mox.StubOutWithMock(self.driver, '_expand_fcp_list')
        self.mox.StubOutWithMock(self.driver, '_get_host_volume_bdms')
        self.mox.StubOutWithMock(self.driver, '_build_connection_info')
        self.driver._expand_fcp_list(farg).AndReturn(set(['0001',
                                                          '0002', '0003']))
        self.driver._get_host_volume_bdms().AndReturn([fake_bdm])
        self.driver._build_connection_info(farg).AndReturn(
                                                        fake_connection_info)
        self.mox.ReplayAll()

        self.driver._init_fcp_pool(fake_fcp_list)
        target_pool = set(['0001', '0003'])
        self.assertTrue(self.driver._fcp_pool == target_pool)
        self.assertEqual({'fcp': '0002', 'count': 1},
                          self.driver._instance_fcp_map.get('fake'))
        self.mox.VerifyAll()
        self.driver._fcp_pool = set()
        self.driver._instance_fcp_map = {}

    def test_build_connection_info_get_none(self):
        self.assertIsNone(self.driver._build_connection_info(None))
        self.assertIsNone(self.driver._build_connection_info({'fake': 'bdm'}))
        invalid_bdm = {"connection_info": 'aaa{"data": {"host": "fake_host",' +
                       '"zvm_fcp": "0001"}}'}
        self.assertIsNone(self.driver._build_connection_info(invalid_bdm))

    def test_build_connection_info(self):
        fake_bdm = {"connection_info": '{"data": {"host": "fake_host",' +
                    '"zvm_fcp": "0001"}}'}
        target_info = {'data': {'host': 'fake_host', 'zvm_fcp': '0001'}}
        connection_info = self.driver._build_connection_info(fake_bdm)
        self.assertTrue(connection_info == target_info)

    def test_get_volume_connector_no_fcp(self):
        fake_instance = {'name': 'fake'}
        self.mox.StubOutWithMock(self.driver, '_get_fcp_from_pool')
        self.driver._get_fcp_from_pool().AndReturn(None)
        self.mox.ReplayAll()

        empty_connector = {'zvm_fcp': None, 'wwpns': [], 'host': ''}
        self.assertEqual(empty_connector,
                         self.driver.get_volume_connector(fake_instance))
        self.mox.VerifyAll()

    def test_get_volume_connector_from_instance(self):
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}

        farg = mox.IgnoreArg()
        self.mox.StubOutWithMock(self.driver, '_get_wwpn')
        self.driver._get_wwpn(farg).AndReturn('00000001')
        self.mox.ReplayAll()

        target_connector = {'host': 'fakenode',
                            'wwpns': ['00000001'],
                            'zvm_fcp': '1faa'}
        self.assertEqual(target_connector,
                         self.driver.get_volume_connector(fake_instance))
        self.mox.VerifyAll()

        self.driver._instance_fcp_map = {}

    def test_get_volume_connector_from_pool(self):
        fake_instance = {'name': 'fake'}
        self.mox.StubOutWithMock(self.driver, '_get_fcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_get_wwpn')
        farg = mox.IgnoreArg()
        self.driver._get_fcp_from_pool().AndReturn('1FAA')
        self.driver._get_wwpn(farg).AndReturn('00000001')
        self.mox.ReplayAll()

        target_connector = {'host': 'fakenode',
                            'wwpns': ['00000001'],
                            'zvm_fcp': '1faa'}
        self.assertEqual(target_connector,
                         self.driver.get_volume_connector(fake_instance))
        self.assertEqual({'fcp': '1faa', 'count': 0},
                         self.driver._instance_fcp_map.get('fake'))
        self.mox.VerifyAll()

        self.driver._instance_fcp_map = {}

    def test_get_volume_connector_no_wwpn(self):
        fake_instance = {'name': 'fake'}
        self.mox.StubOutWithMock(self.driver, '_get_fcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_get_wwpn')
        farg = mox.IgnoreArg()
        self.driver._get_fcp_from_pool().AndReturn('0001')
        self.driver._get_wwpn(farg).AndReturn(None)
        self.mox.ReplayAll()

        empty_connector = {'zvm_fcp': None, 'wwpns': [], 'host': ''}
        self.assertEqual(empty_connector,
                         self.driver.get_volume_connector(fake_instance))
        self.mox.VerifyAll()

    def test_get_wwpn_get_none(self):
        self.mox.StubOutWithMock(self.driver, '_list_fcp_details')
        self.mox.StubOutWithMock(self.driver, '_extract_wwpn_from_fcp_info')

        farg = mox.IgnoreArg()
        self.driver._list_fcp_details(farg).AndReturn('fake_active')
        self.driver._extract_wwpn_from_fcp_info(farg, farg).AndReturn(None)
        self.driver._list_fcp_details(farg).AndReturn('fake_free')
        self.driver._extract_wwpn_from_fcp_info(farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.assertIsNone(self.driver._get_wwpn('1FAA'))
        self.mox.VerifyAll()

    def test_get_wwpn(self):
        self.mox.StubOutWithMock(self.driver, '_list_fcp_details')
        self.mox.StubOutWithMock(self.driver, '_extract_wwpn_from_fcp_info')

        farg = mox.IgnoreArg()
        self.driver._list_fcp_details(farg).AndReturn('fake_active')
        self.driver._extract_wwpn_from_fcp_info(farg, farg).AndReturn(None)
        self.driver._list_fcp_details(farg).AndReturn('fake_free')
        self.driver._extract_wwpn_from_fcp_info(farg, farg).AndReturn(
                '20076D8500003C11')
        self.mox.ReplayAll()

        self.assertEqual(self.driver._get_wwpn('1FBC'), '20076D8500003C11')
        self.mox.VerifyAll()

    def test_list_fcp_details_get_none(self):
        self.mox.StubOutWithMock(self.driver, '_xcat_rinv')
        self.driver._xcat_rinv(mox.IgnoreArg()).AndReturn({'info': None})
        self.mox.ReplayAll()

        self.assertIsNone(self.driver._list_fcp_details('free'))
        self.mox.VerifyAll()

    def test_list_fcp_details(self):
        fake_rsp = {'info': [['line1\nline2\nline3\nline4\nline5\n']]}
        self.mox.StubOutWithMock(self.driver, '_xcat_rinv')
        self.driver._xcat_rinv(mox.IgnoreArg()).AndReturn(fake_rsp)
        self.mox.ReplayAll()

        target = ['line1', 'line2', 'line3', 'line4', 'line5']
        self.assertTrue(target == self.driver._list_fcp_details('active'))
        self.mox.VerifyAll()

    def test_extract_wwpn_from_fcp_info_get_none(self):
        line1 = 'opnstk1: FCP device number: 1FAC'
        line2 = 'opnstk1:   Status: Active'
        line3 = 'opnstk1:   NPIV world wide port number: NONE'
        line4 = 'opnstk1:   Channel path ID: 16'
        line5 = 'opnstk1:   Physical world wide port number: 20076D8500003C11'
        line6 = 'opnstk1: FCP device number: 1FAD'
        line7 = 'opnstk1:   Status: Active'
        line8 = 'opnstk1:   NPIV world wide port number: NONE'
        line9 = 'opnstk1:   Channel path ID: 16'
        lineA = 'opnstk1:   Physical world wide port number: 20076D8500003C12'
        fake_fcp_info = [line1, line2, line3, line4, line5,
                         line6, line7, line8, line9, lineA]
        self.assertIsNone(self.driver._extract_wwpn_from_fcp_info(
                '1FBC', fake_fcp_info))

    def test_extract_wwpn_from_fcp_info_NPIV_wwpn(self):
        line1 = 'opnstk1: FCP device number: 1FAC'
        line2 = 'opnstk1:   Status: Active'
        line3 = 'opnstk1:   NPIV world wide port number: 20076D8500003C00'
        line4 = 'opnstk1:   Channel path ID: 16'
        line5 = 'opnstk1:   Physical world wide port number: 20076D8500003C11'
        line6 = 'opnstk1: FCP device number: 1FAD'
        line7 = 'opnstk1:   Status: Active'
        line8 = 'opnstk1:   NPIV world wide port number: 20076D8500003C01'
        line9 = 'opnstk1:   Channel path ID: 16'
        lineA = 'opnstk1:   Physical world wide port number: 20076D8500003C11'
        fake_fcp_info = [line1, line2, line3, line4, line5,
                         line6, line7, line8, line9, lineA]
        target_wwpn = '20076D8500003C01'
        self.assertEqual(self.driver._extract_wwpn_from_fcp_info(
                '1FAD', fake_fcp_info), target_wwpn)

    def test_extract_wwpn_from_fcp_info_physical_wwpn(self):
        line1 = 'opnstk1: FCP device number: 1FAC'
        line2 = 'opnstk1:   Status: Active'
        line3 = 'opnstk1:   NPIV world wide port number: NONE'
        line4 = 'opnstk1:   Channel path ID: 16'
        line5 = 'opnstk1:   Physical world wide port number: 20076D8500003C10'
        line6 = 'opnstk1: FCP device number: 1FAD'
        line7 = 'opnstk1:   Status: Active'
        line8 = 'opnstk1:   NPIV world wide port number: NONE'
        line9 = 'opnstk1:   Channel path ID: 16'
        lineA = 'opnstk1:   Physical world wide port number: 20076D8500003C11'
        fake_fcp_info = [line1, line2, line3, line4, line5,
                         line6, line7, line8, line9, lineA]
        target_wwpn = '20076D8500003C11'
        self.assertEqual(self.driver._extract_wwpn_from_fcp_info(
                '1FAD', fake_fcp_info), target_wwpn)

    def test_get_wwpn_from_line_get_none(self):
        wwpn_info = 'opnstk1:   NPIV world wide port number: NONE'
        self.assertIsNone(self.driver._get_wwpn_from_line(wwpn_info))
        wwpn_info = 'opnstk1:   NPIV world wide port number: none'
        self.assertIsNone(self.driver._get_wwpn_from_line(wwpn_info))
        wwpn_info = 'opnstk1:   NPIV world wide port number: '
        self.assertIsNone(self.driver._get_wwpn_from_line(wwpn_info))

    def test_get_wwpn_from_line(self):
        wwpn_info = 'opnstk1:   NPIV world wide port number: 20076D8500003C00'
        target_wwpn = '20076D8500003C00'
        self.assertEqual(self.driver._get_wwpn_from_line(wwpn_info),
                         target_wwpn)

    def test_get_fcp_from_pool_get_none(self):
        self.driver._fcp_pool = set()
        self.mox.StubOutWithMock(self.driver, '_init_fcp_pool')
        self.driver._init_fcp_pool(mox.IgnoreArg()).AndReturn(None)
        self.mox.ReplayAll()
        self.assertIsNone(self.driver._get_fcp_from_pool())
        self.mox.VerifyAll()

    def test_get_fcp_from_pool_no_init(self):
        self.driver._fcp_pool = set(['1FAA'])
        self.assertEqual(self.driver._get_fcp_from_pool(), '1FAA')
        self.driver._fcp_pool = set()

    def test_get_fcp_from_pool_do_init(self):
        def set_fcp_pool(*params, **named_params):
            self.driver._fcp_pool = set(['1FAA'])

        self.mox.StubOutWithMock(self.driver, '_init_fcp_pool')
        self.driver._init_fcp_pool(mox.IgnoreArg()).WithSideEffects(
                set_fcp_pool)
        self.mox.ReplayAll()
        self.assertEqual(self.driver._get_fcp_from_pool(), '1FAA')
        self.mox.VerifyAll()
        self.driver._fcp_pool = set()

    def test_extract_connection_info_error(self):
        fake_connection_info = 'fake_info'
        self.assertRaises(exception.ZVMVolumeError,
                          self.driver._extract_connection_info,
                          None, fake_connection_info)

    def test_extract_connection_info_no_context(self):
        fake_connection_info = {'data': {'target_lun': 10,
                                         'target_wwn': '0000000B',
                                         'zvm_fcp': '1FAA'}}
        target_info = ('000a000000000000', '0000000b', '0G', '1faa')
        self.assertEqual(target_info,
                         self.driver._extract_connection_info(
                                None, fake_connection_info))

    def test_extract_connection_info_with_context(self):
        fake_connection_info = {'data': {'target_lun': 10,
                                         'target_wwn': '0000000B',
                                         'volume_id': 'fake_id',
                                         'zvm_fcp': '1FAA'}}
        fake_context = 'fake_context'
        self.mox.StubOutWithMock(self.driver, '_get_volume_by_id')

        farg = mox.IgnoreArg()
        self.driver._get_volume_by_id(farg, farg).AndReturn({'size': 2})
        self.mox.ReplayAll()

        target_info = ('000a000000000000', '0000000b', '2G', '1faa')
        self.assertEqual(target_info,
                         self.driver._extract_connection_info(
                                fake_context, fake_connection_info))
        self.mox.VerifyAll()

    def test_extract_connection_info_multipath(self):
        fake_connection_info = {'data': {'target_lun': 10,
                                         'target_wwn': ['00000B', '00000E'],
                                         'volume_id': 'fake_id',
                                         'zvm_fcp': '1FAA'}}
        fake_context = 'fake_context'
        self.mox.StubOutWithMock(self.driver, '_get_volume_by_id')

        farg = mox.IgnoreArg()
        self.driver._get_volume_by_id(farg, farg).AndReturn({'size': 2})
        self.mox.ReplayAll()

        target_info = ('000a000000000000', '00000b;00000e', '2G', '1faa')
        self.assertEqual(target_info,
                         self.driver._extract_connection_info(
                                fake_context, fake_connection_info))
        self.mox.VerifyAll()

    def test_attach_volume_active_error_no_rollback(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        fake_mountpoint = '/dev/vdd'
        self.driver._instance_fcp_map = {}
        rollback = False

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp')
        self.mox.StubOutWithMock(self.driver, '_create_mountpoint')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._add_zfcp(farg, farg, farg, farg, farg).AndReturn(None)
        self.driver._create_mountpoint(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.attach_volume_active, farg,
                          farg, fake_instance, fake_mountpoint, rollback)
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_active_error_rollback_and_detach(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_mountpoint = '/dev/vdd'
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp')
        self.mox.StubOutWithMock(self.driver, '_create_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_detach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._add_zfcp(farg, farg, farg, farg, farg).AndReturn(None)
        self.driver._create_mountpoint(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_mountpoint(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_zfcp(farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_zfcp_from_pool(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._detach_device(farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.attach_volume_active, farg, farg,
                          fake_instance, fake_mountpoint, rollback)
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_active_error_rollback_no_detach(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_mountpoint = '/dev/vdd'
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp')
        self.mox.StubOutWithMock(self.driver, '_create_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._add_zfcp(farg, farg, farg, farg, farg).AndReturn(None)
        self.driver._create_mountpoint(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_mountpoint(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_zfcp(farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_zfcp_from_pool(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.attach_volume_active, farg,
                          farg, fake_instance, fake_mountpoint, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_active(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_mountpoint = '/dev/vdd'
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp')
        self.mox.StubOutWithMock(self.driver, '_create_mountpoint')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._add_zfcp(farg, farg, farg, farg, farg).AndReturn(None)
        self.driver._create_mountpoint(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.mox.ReplayAll()

        self.driver.attach_volume_active(farg, farg, fake_instance,
                                         fake_mountpoint, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_active_no_mountpoint(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp')
        self.mox.StubOutWithMock(self.driver, '_create_mountpoint')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._add_zfcp(farg, farg, farg, farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.attach_volume_active(farg, farg, fake_instance,
                                         None, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_active_error_no_rollback(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        fake_mountpoint = '/dev/vdd'
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = False

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_detach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_mountpoint(farg, farg).AndReturn(None)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._detach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.detach_volume_active, farg,
                          fake_instance, fake_mountpoint, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_active_error_rollback_with_mountpoint(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        fake_mountpoint = '/dev/vdd'
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_detach_device')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp')
        self.mox.StubOutWithMock(self.driver, '_create_mountpoint')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_mountpoint(farg, farg).AndReturn(None)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._detach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._add_zfcp(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._create_mountpoint(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.detach_volume_active, farg,
                          fake_instance, fake_mountpoint, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_active_error_rollback_no_mountpoint(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_detach_device')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._detach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._add_zfcp(farg, farg, farg, farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.detach_volume_active,
                          farg, fake_instance, None, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_active_and_detach_fcp(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_mountpoint = '/dev/vdd'
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = False

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_detach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_mountpoint(farg, farg).AndReturn(None)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._detach_device(farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.detach_volume_active(farg, fake_instance,
                                         fake_mountpoint, rollback)
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_active_and_reserve_fcp(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_mountpoint = '/dev/vdd'
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 2}}
        rollback = False

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_mountpoint')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_mountpoint(farg, farg).AndReturn(None)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.detach_volume_active(farg, fake_instance,
                                         fake_mountpoint, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_inactive_error_no_rollback(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {}
        rollback = False

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_allocate_zfcp')
        self.mox.StubOutWithMock(self.driver, '_notice_attach')
        self.mox.StubOutWithMock(self.driver, '_attach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._allocate_zfcp(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._notice_attach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._attach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.attach_volume_inactive, farg,
                          farg, fake_instance, farg, rollback)
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_inactive_error_rollback_and_detach(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_allocate_zfcp')
        self.mox.StubOutWithMock(self.driver, '_notice_attach')
        self.mox.StubOutWithMock(self.driver, '_attach_device')
        self.mox.StubOutWithMock(self.driver, '_notice_detach')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_detach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._allocate_zfcp(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._notice_attach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._attach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._notice_detach(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_zfcp_from_pool(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._detach_device(farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.attach_volume_inactive, farg,
                          farg, fake_instance, farg, rollback)
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_inactive_error_rollback_no_detach(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_allocate_zfcp')
        self.mox.StubOutWithMock(self.driver, '_notice_attach')
        self.mox.StubOutWithMock(self.driver, '_notice_detach')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._allocate_zfcp(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._notice_attach(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._notice_detach(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._remove_zfcp_from_pool(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.attach_volume_inactive, farg,
                          farg, fake_instance, farg, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_inactive(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_allocate_zfcp')
        self.mox.StubOutWithMock(self.driver, '_notice_attach')
        self.mox.StubOutWithMock(self.driver, '_attach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._allocate_zfcp(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._notice_attach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._attach_device(farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.attach_volume_inactive(farg, farg, fake_instance,
                                           farg, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_attach_volume_inactive_no_attach(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_allocate_zfcp')
        self.mox.StubOutWithMock(self.driver, '_notice_attach')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(None)
        self.driver._allocate_zfcp(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._notice_attach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.mox.ReplayAll()

        self.driver.attach_volume_inactive(farg, farg, fake_instance,
                                           farg, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_inactive_error_no_rollback(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = False

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_notice_detach')
        self.mox.StubOutWithMock(self.driver, '_detach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._notice_detach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._detach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.detach_volume_inactive,
                          farg, fake_instance, farg, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_inactive_error_detach_rollback(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_notice_detach')
        self.mox.StubOutWithMock(self.driver, '_detach_device')
        self.mox.StubOutWithMock(self.driver, '_attach_device')
        self.mox.StubOutWithMock(self.driver, '_notice_attach')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_allocate_zfcp')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._notice_detach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._detach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._attach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._notice_attach(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndReturn(True)
        self.driver._allocate_zfcp(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.detach_volume_inactive,
                          farg, fake_instance, farg, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_inactive_error_no_detach_rollback(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 2}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_notice_detach')
        self.mox.StubOutWithMock(self.driver, '_attach_device')
        self.mox.StubOutWithMock(self.driver, '_notice_attach')
        self.mox.StubOutWithMock(self.driver, '_add_zfcp_to_pool')
        self.mox.StubOutWithMock(self.driver, '_allocate_zfcp')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._notice_detach(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._attach_device(farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._notice_attach(farg, farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._add_zfcp_to_pool(farg, farg, farg, farg).AndRaise(
                exception.ZVMVolumeError(msg='No msg'))
        self.driver._allocate_zfcp(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMVolumeError,
                          self.driver.detach_volume_inactive,
                          farg, fake_instance, farg, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_inactive_and_detach(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_notice_detach')
        self.mox.StubOutWithMock(self.driver, '_detach_device')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._notice_detach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.driver._detach_device(farg, farg).AndReturn(None)
        self.mox.ReplayAll()

        self.driver.detach_volume_inactive(farg, fake_instance,
                                           farg, rollback)
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_detach_volume_inactive_no_detach(self):
        fake_info = ('lun', 'wwpn', '1G', '1faa')
        fake_instance = {'name': 'fake'}
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 2}}
        rollback = True

        self.mox.StubOutWithMock(self.driver, '_extract_connection_info')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp')
        self.mox.StubOutWithMock(self.driver, '_remove_zfcp_from_pool')
        self.mox.StubOutWithMock(self.driver, '_notice_detach')

        farg = mox.IgnoreArg()
        self.driver._extract_connection_info(farg, farg).AndReturn(fake_info)
        self.driver._remove_zfcp(farg, farg, farg, farg).AndReturn(None)
        self.driver._remove_zfcp_from_pool(farg, farg).AndReturn(None)
        self.driver._notice_detach(farg, farg, farg, farg, farg).AndReturn(
                None)
        self.mox.ReplayAll()

        self.driver.detach_volume_inactive(farg, fake_instance, farg, rollback)
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()
        self.driver._instance_fcp_map = {}

    def test_expand_fcp_list(self):
        fcp_list = '01aF;01b9-01BB;01BA-01BC;0000;9999;AAAA;FFFF;1;a'
        target_list = set(['01af', '01b9', '01ba', '01bb', '01bc', '0000',
                           '9999', 'aaaa', 'ffff', '0001', '000a'])
        self.assertEqual(target_list, self.driver._expand_fcp_list(fcp_list))

    def test_is_fcp_in_use_no_record(self):
        self.driver._instance_fcp_map = {}
        fake_instance = {'name': 'fake'}
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()

    def test_is_fcp_in_use_reserved(self):
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 0}}
        fake_instance = {'name': 'fake'}
        self.assertFalse(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()

    def test_is_fcp_in_use_only_one(self):
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 1}}
        fake_instance = {'name': 'fake'}
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()

    def test_is_fcp_in_use_multi_volume(self):
        self.driver._instance_fcp_map = {'fake': {'fcp': '1faa', 'count': 3}}
        fake_instance = {'name': 'fake'}
        self.assertTrue(self.driver._is_fcp_in_use(fake_instance, '1faa'))
        self.mox.VerifyAll()


class ZVMImageOPTestCases(ZVMTestCase):
    """Basic test cases of image operation."""

    def setUp(self):
        super(ZVMImageOPTestCases, self).setUp()
        self.imageop = imageop.ZVMImages()
        self.res_data = {"data": [{"data": [["song017c: Filesystem     "
            "Size  Used Avail Use% Mounted on\nsong017c: "
            "/dev/dasda1     3.0G  1.7G  1.2G  60% /"]]},
            {"errorcode": [["0"]], "NoErrorPrefix": [["1"]],
            "error": [["song017c: @@@@@@@@@@@@@@@@@@@@@@@@@@@"
            "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n", "song017c: @    "
            "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"
            "@\n", "song017c: @@@@@@@@@@@@@@@@@"
            "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n",
            "song017c: IT IS POSSIBLE THAT SOMEONE IS "
            "DOING SOMETHING NASTY!\n",
            "song017c: Someone could be eavesdropping on "
            "you right now (man-in-the-middle attack)!\n",
            "song017c: It is also possible that a host key "
            "has just been changed.\n",
            "song017c: The fingerprint for the RSA key sent "
            "by the remote host is\nsong017c: d9:9c:71:ed:be:8e:"
            "cd:0e:b1:1a:f3:fe:a8:30:84:8b.\n",
            "song017c: Please contact your administrator.\n",
            "song017c: Add host key in /root/.ssh/known_hosts"
            "to get rid of this message.\n",
            "song017c: /root/.ssh/known_hosts:22\n",
            "song017c: Password authentication is disabled "
            "to avoid man-in-the-middle attacks.\n",
            "song017c: Keyboard-interactive authentication "
            "is disabled to avoid man-the-middle attacks.\n"]]},
            {"errorcode": ["0"]}]}

    def test_get_imgcapture_needed_in_compression_0(self):
        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        zvmutils.xcat_request('PUT', mox.IgnoreArg(),
                        mox.IgnoreArg()).AndReturn(self.res_data['data'][0])
        self.mox.ReplayAll()
        size = self.imageop.get_imgcapture_needed(self.instance)
        self.assertEqual(size, float(3.0) * 2)

        self.mox.VerifyAll()

    def test_get_imgcapture_needed_in_compression_6(self):
        self.mox.StubOutWithMock(zvmutils, 'xcat_request')
        zvmutils.xcat_request('PUT', mox.IgnoreArg(),
                        mox.IgnoreArg()).AndReturn(self.res_data['data'][0])
        self.mox.ReplayAll()
        self.flags(zvm_image_compression_level='6')
        size = self.imageop.get_imgcapture_needed(self.instance)
        self.assertEqual(size, float(1.7) * 2)

        self.mox.VerifyAll()

    def test_get_image_file_path_from_image_name(self):
        image_name_xcat = "rhel65-s390x-netboot-img_uuid"

        self.mox.StubOutWithMock(zvmutils, 'xcat_cmd_gettab')
        zvmutils.xcat_cmd_gettab("linuximage", "imagename", image_name_xcat,
             "rootimgdir").AndReturn('/install/netboot/rhel65/s390x/img_uuid')
        self.mox.ReplayAll()

        image_file_path = self.imageop.get_image_file_path_from_image_name(
                                                            image_name_xcat)
        self.mox.VerifyAll()
        self.assertEqual(image_file_path,
                         '/install/netboot/rhel65/s390x/img_uuid')

    def test_delete_image_from_xcat(self):
        image_name_xcat = "rhel65-s390x-netboot-img_uuid"

        self.mox.StubOutWithMock(self.imageop, '_delete_image_file_from_xcat')
        self.mox.StubOutWithMock(self.imageop,
                                 '_delete_image_object_from_xcat')
        self.imageop._delete_image_file_from_xcat(image_name_xcat)
        self.imageop._delete_image_object_from_xcat(image_name_xcat)
        self.mox.ReplayAll()

        self.imageop.delete_image_from_xcat(image_name_xcat)
        self.mox.VerifyAll()

    def test_get_image_file_name_not_exist(self):
        self.mox.StubOutWithMock(os.path, 'exists')
        os.path.exists('/fake').AndReturn(False)
        self.mox.ReplayAll()

        self.assertRaises(exception.ZVMImageError,
                          self.imageop.get_image_file_name, '/fake')
        self.mox.VerifyAll()

    @mock.patch('nova.utils.execute')
    def test_get_root_disk_units(self, mk_exec):
        mk_exec.return_value = (''.join(['CKD', '1' * 160]), None)
        self.assertEqual(111111111111,
                         self.imageop.get_root_disk_units('/fake'))

    @mock.patch('nova.utils.execute')
    def test_get_root_disk_units_cmd_err(self, mk_exec):
        mk_exec.side_effect = processutils.ProcessExecutionError
        self.assertRaises(exception.ZVMImageError,
                         self.imageop.get_root_disk_units, '/fake')

    @mock.patch('nova.utils.execute')
    def test_get_root_disk_units_value_err(self, mk_exec):
        mk_exec.return_value = (''.join(['CKD', 's' * 160]), None)
        self.assertRaises(exception.ZVMImageError,
                         self.imageop.get_root_disk_units, '/fake')

    @mock.patch('nova.utils.execute')
    def test_get_root_disk_units_invalid_type(self, mk_exec):
        mk_exec.return_value = ('1' * 160, None)
        self.assertRaises(exception.ZVMImageError,
                         self.imageop.get_root_disk_units, '/fake')


class ZVMDistTestCases(test.TestCase):
    def setUp(self):
        super(ZVMDistTestCases, self).setUp()

        self.rhel6 = dist.rhel6()
        self.rhel7 = dist.rhel7()
        self.sles11 = dist.sles11()
        self.sles12 = dist.sles12()
        self.support_list = [self.rhel6, self.rhel7,
                             self.sles11, self.sles12]

    def test_get_znetconfig_contents(self):
        for v in self.support_list:
            contents = v.get_znetconfig_contents()
            self.assertTrue(contents > 0)

    def test_get_dns_filename(self):
        for v in self.support_list:
            v._get_dns_filename()

    def test_get_cmd_str(self):
        for v in self.support_list:
            v._get_cmd_str('0', '0', '0')

    def test_get_route_str(self):
        for v in self.support_list:
            v._get_route_str(0)

    def test_get_network_file_path(self):
        for v in self.support_list:
            contents = v._get_network_file_path()
            self.assertTrue(contents > 0)

    def test_get_change_passwd_command(self):
        for v in self.support_list:
            contents = v.get_change_passwd_command('0')
            self.assertTrue(contents > 0)

    def test_get_device_name(self):
        for v in self.support_list:
            contents = v._get_device_name(0)
            self.assertTrue(contents > 0)

    def test_get_cfg_str(self):
        for v in self.support_list:
            v._get_cfg_str('0', '0', '0', '0', '0', '0', '0')

    def test_get_device_filename(self):
        for v in self.support_list:
            contents = v._get_device_filename(0)
            self.assertTrue(contents > 0)

    def test_get_udev_configuration(self):
        for v in self.support_list:
            v._get_udev_configuration('0', '0')

    def test_append_udev_info(self):
        for v in self.support_list:
            v._append_udev_info([], '0', '0', '0')


class ZVMDistRhel7TestCases(test.TestCase):
    def setUp(self):
        super(ZVMDistRhel7TestCases, self).setUp()

        self.rhel7 = dist.rhel7()

    def test_get_device_name(self):
        result = ['enccw0.0.1000', 'enccw0.0.1003', 'enccw0.0.100c']
        devices = [0, 1, 4]
        for (index, s) in enumerate(devices):
            contents = self.rhel7._get_device_name(s)
            self.assertEqual(result[index], contents)

        result = ['enccw0.0.0800', 'enccw0.0.0803', 'enccw0.0.080c']
        temp = const.ZVM_DEFAULT_NIC_VDEV
        const.ZVM_DEFAULT_NIC_VDEV = '800'
        devices = [0, 1, 4]
        for (index, s) in enumerate(devices):
            contents = self.rhel7._get_device_name(s)
            self.assertEqual(result[index], contents)
        const.ZVM_DEFAULT_NIC_VDEV = temp

    def test_get_device_filename(self):
        result = ['ifcfg-enccw0.0.1000',
                  'ifcfg-enccw0.0.1003',
                  'ifcfg-enccw0.0.100c']
        devices = [0, 1, 4]
        for (index, s) in enumerate(devices):
            contents = self.rhel7._get_device_filename(s)
            self.assertEqual(result[index], contents)

        result = ['ifcfg-enccw0.0.0800',
                  'ifcfg-enccw0.0.0803',
                  'ifcfg-enccw0.0.080c']
        temp = const.ZVM_DEFAULT_NIC_VDEV
        const.ZVM_DEFAULT_NIC_VDEV = '800'
        devices = [0, 1, 4]
        for (index, s) in enumerate(devices):
            contents = self.rhel7._get_device_filename(s)
            self.assertEqual(result[index], contents)
        const.ZVM_DEFAULT_NIC_VDEV = temp


class ZVMDistManagerTestCases(test.TestCase):
    def setUp(self):
        super(ZVMDistManagerTestCases, self).setUp()
        self.dist_manager = dist.ListDistManager()

    def test_rhel6(self):
        os_versions = ['rhel6.5', 'rhel6', 'redhat6.4', 'redhat6'
                       'red hat6', 'red hat6.5', 'RhEl6', 'RedHat6.5',
                       'REDHAT6.4']
        for v in os_versions:
            d = self.dist_manager.get_linux_dist(v)()
            self.assertTrue(isinstance(d, dist.rhel6))

    def test_rhel7(self):
        os_versions = ['rhel7.1', 'red hat7.1', 'redhat7.1', 'RHEL7.1']
        for v in os_versions:
            d = self.dist_manager.get_linux_dist(v)()
            self.assertTrue(isinstance(d, dist.rhel7))

    def test_sles11(self):
        os_versions = ['sles11sp2', 'sles11sp3', 'sles11.2', 'sles11.3',
                       'Sles11sp3', 'SLES11.2']
        for v in os_versions:
            d = self.dist_manager.get_linux_dist(v)()
            self.assertTrue(isinstance(d, dist.sles11))

    def test_sles12(self):
        os_versions = ['sles12', 'sles12.0', 'Sles12', 'SLES12.0']
        for v in os_versions:
            d = self.dist_manager.get_linux_dist(v)()
            self.assertTrue(isinstance(d, dist.sles12))

    def test_invalid(self):
        os_versions = ['', 'sles 11.0', 'sles13.0', 'sles10', 'rhel8',
                       'rhel 6', 'fake', 'SELS12.0']
        for v in os_versions:
            self.assertRaises(exception.ZVMImageError,
                              self.dist_manager.get_linux_dist, v)
