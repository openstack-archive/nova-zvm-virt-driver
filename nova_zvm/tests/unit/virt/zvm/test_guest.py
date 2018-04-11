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

import copy
import mock
import os
import six

from nova.compute import power_state as compute_power_state
from nova import conf
from nova import context
from nova import exception
from nova.network import model as network_model
from nova import objects
from nova import test
from nova.tests.unit import fake_instance
from nova.tests import uuidsentinel
from nova.virt import fake
from nova.virt.zvm import driver
from nova.virt.zvm import guest


CONF = conf.CONF


class TestZVMGuestOp(test.NoDBTestCase):
    def setUp(self):
        super(TestZVMGuestOp, self).setUp()
        self.flags(cloud_connector_url='https://1.1.1.1:1111',
                   image_tmp_path='/test/image',
                   reachable_timeout=300, group='zvm')
        self.flags(my_ip='192.168.1.1',
                   instance_name_template='test%04x')
        with test.nested(
            mock.patch('nova.virt.zvm.utils.ConnectorClient.call'),
            mock.patch('pwd.getpwuid'),
        ) as (mcall, getpwuid):
            getpwuid.return_value = mock.Mock(pw_name='test')
            mcall.return_value = {'hypervisor_hostname': 'TESTHOST',
                                  'ipl_time': 'TESTTIME'}
            self._driver = driver.ZVMDriver(fake.FakeVirtAPI())
            self._hypervisor = self._driver._hypervisor

        self._context = context.RequestContext('fake_user', 'fake_project')
        self._image_id = uuidsentinel.imag_id

        self._instance_values = {
            'display_name': 'test',
            'uuid': uuidsentinel.inst_id,
            'vcpus': 1,
            'memory_mb': 1024,
            'image_ref': self._image_id,
            'root_gb': 0,
        }
        self._instance = fake_instance.fake_instance_obj(
                                self._context, **self._instance_values)
        self._guest = guest.Guest(self._hypervisor, self._instance,
                                  self._driver.virtapi)
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

        self.mock_update_task_state = mock.Mock()

    def test_private_mapping_power_state(self):
        status = self._guest._mapping_power_state('on')
        self.assertEqual(compute_power_state.RUNNING, status)
        status = self._guest._mapping_power_state('off')
        self.assertEqual(compute_power_state.SHUTDOWN, status)
        status = self._guest._mapping_power_state('bad')
        self.assertEqual(compute_power_state.NOSTATE, status)

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_get_info_err_InstanceNotFound(self, call):
        call.side_effect = exception.NovaException(results={'overallRC': 404})
        self.assertRaises(exception.InstanceNotFound, self._guest.get_info)

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_get_info_err_general(self, call):
        call.side_effect = exception.NovaException(results={'overallRC': 500})
        self.assertRaises(exception.NovaException, self._guest.get_info)

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_get_info(self, call):
        call.return_value = 'on'
        info = self._guest.get_info()
        call.assert_called_once_with('guest_get_power_state',
                                     self._instance['name'])
        self.assertEqual(info.state, compute_power_state.RUNNING)

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_private_get_image_info_err(self, call):
        call.side_effect = exception.NovaException(results={'overallRC': 500})
        self.assertRaises(exception.NovaException,
                          self._guest._get_image_info,
                          'context', 'image_meta_id', 'os_distro')

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    @mock.patch('nova.virt.zvm.guest.Guest._import_spawn_image')
    def test_private_get_image_info(self, image_import, call):
        call_response = []
        call_response.append(exception.NovaException(results=
                                                     {'overallRC': 404}))
        call_response.append('Query_Result')
        call.side_effect = call_response
        self._guest._get_image_info('context', 'image_meta_id', 'os_distro')
        image_import.assert_called_once_with('context', 'image_meta_id',
                                             'os_distro')
        call.assert_has_calls([
            mock.call('image_query', imagename='image_meta_id'),
            mock.call('image_query', imagename='image_meta_id')
        ])

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_private_get_image_info_exist(self, call):
        call.return_value = 'image-info'
        res = self._guest._get_image_info('context', 'image_meta_id',
                                          'os_distro')
        call.assert_called_once_with('image_query', imagename='image_meta_id')
        self.assertEqual('image-info', res)

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
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

        res1, res2 = self._guest._set_disk_list(_inst, self._image_meta.id,
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

    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_private_setup_network(self, call):
        inst_nets = []
        _net = {'ip_addr': '192.168.0.100',
                'gateway_addr': '192.168.0.1',
                'cidr': '192.168.0.1/24',
                'mac_addr': 'DE:AD:BE:EF:00:00',
                'nic_id': None}
        inst_nets.append(_net)
        self._guest._setup_network('vm_name', 'os_distro',
                                     self._network_info,
                                     self._instance)
        call.assert_called_once_with('guest_create_network_interface',
                                     'vm_name', 'os_distro', inst_nets)

    @mock.patch('nova.virt.images.fetch')
    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_private_import_spawn_image(self, call, fetch):

        image_name = CONF.zvm.image_tmp_path + '/image_name'
        image_url = "file://" + image_name
        image_meta = {'os_version': 'os_version'}
        with mock.patch('os.path.exists', side_effect=[False]):
            self._guest._import_spawn_image(self._context, 'image_name',
                                            'os_version')
        fetch.assert_called_once_with(self._context, 'image_name',
                                      image_name)
        call.assert_called_once_with('image_import', 'image_name', image_url,
                        image_meta, 'test@192.168.1.1')

    @mock.patch('nova.virt.zvm.hypervisor.Hypervisor.guest_exists')
    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_destroy(self, call, guest_exists):
        guest_exists.return_value = True
        self._guest.destroy(self._context, network_info=self._network_info)
        call.assert_called_once_with('guest_delete', self._instance['name'])

    @mock.patch('nova.virt.zvm.hypervisor.Hypervisor.guest_exists')
    @mock.patch('nova.compute.manager.ComputeVirtAPI.wait_for_instance_event')
    @mock.patch('nova.virt.zvm.guest.Guest._setup_network')
    @mock.patch('nova.virt.zvm.guest.Guest._set_disk_list')
    @mock.patch('nova.virt.zvm.utils.generate_configdrive')
    @mock.patch('nova.virt.zvm.guest.Guest._get_image_info')
    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_spawn(self, call, get_image_info, gen_conf_file, set_disk_list,
                   setup_network, mock_wait, mock_exists):
        _bdi = copy.copy(self._block_device_info)
        get_image_info.return_value = [{'imagename': 'image_name'}]
        gen_conf_file.return_value = 'transportfiles'
        set_disk_list.return_value = 'disk_list', 'eph_list'
        mock_exists.return_value = False
        self._guest.spawn(self._context, self._image_meta,
                          injected_files=None, admin_password=None,
                          allocations=None, network_info=self._network_info,
                          block_device_info=_bdi, flavor=self._flavor)
        gen_conf_file.assert_called_once_with(self._context, self._instance,
                                              None, None)
        get_image_info.assert_called_once_with(self._context,
                                    self._image_meta.id,
                                    self._image_meta.properties.os_distro)
        set_disk_list.assert_called_once_with(self._instance, 'image_name',
                                              _bdi)
        setup_network.assert_called_once_with(self._instance.name,
                                self._image_meta.properties.os_distro,
                                self._network_info, self._instance)

        call.assert_has_calls([
            mock.call('guest_create', self._instance.name,
                      1, 1024, disk_list='disk_list'),
            mock.call('guest_deploy', self._instance.name, 'image_name',
                      'transportfiles', 'test@192.168.1.1'),
            mock.call('guest_config_minidisks', self._instance.name,
                      'eph_list'),
            mock.call('guest_start', self._instance.name)
        ])

    @mock.patch.object(six.moves.builtins, 'open')
    @mock.patch('nova.image.glance.get_remote_image_service')
    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    def test_snapshot(self, call, get_image_service, mock_open):
        image_service = mock.Mock()
        image_id = 'e9ee1562-3ea1-4cb1-9f4c-f2033000eab1'
        get_image_service.return_value = (image_service, image_id)
        call_resp = ['', {"os_version": "rhel7.2",
                          "dest_url": "file:///path/to/target"}, '']
        call.side_effect = call_resp
        new_image_meta = {
            'is_public': False,
            'status': 'active',
            'properties': {
                 'image_location': 'snapshot',
                 'image_state': 'available',
                 'owner_id': self._instance['project_id'],
                 'os_distro': call_resp[1]['os_version'],
                 'architecture': 's390x',
                 'hypervisor_type': 'zvm'
            },
            'disk_format': 'raw',
            'container_format': 'bare',
        }
        image_path = os.path.join(os.path.normpath(
                            CONF.zvm.image_tmp_path), image_id)
        dest_path = "file://" + image_path

        self._guest.snapshot(self._context, image_id,
                             self.mock_update_task_state)
        get_image_service.assert_called_with(self._context, image_id)

        mock_open.assert_called_once_with(image_path, 'r')
        ret_file = mock_open.return_value.__enter__.return_value
        image_service.update.assert_called_once_with(self._context,
                                                     image_id,
                                                     new_image_meta,
                                                     ret_file,
                                                     purge_props=False)
        self.mock_update_task_state.assert_has_calls([
            mock.call(task_state='image_pending_upload'),
            mock.call(expected_state='image_pending_upload',
                      task_state='image_uploading')
        ])
        call.assert_has_calls([
            mock.call('guest_capture', self._instance.name, image_id),
            mock.call('image_export', image_id, dest_path, 'test@192.168.1.1'),
            mock.call('image_delete', image_id)
        ])

    @mock.patch('nova.image.glance.get_remote_image_service')
    @mock.patch('nova.virt.zvm.hypervisor.Hypervisor.guest_capture')
    def test_snapshot_capture_fail(self, mock_capture, get_image_service):
        image_service = mock.Mock()
        image_id = 'e9ee1562-3ea1-4cb1-9f4c-f2033000eab1'
        get_image_service.return_value = (image_service, image_id)
        mock_capture.side_effect = exception.ZVMDriverException(error='error')

        self.assertRaises(exception.ZVMDriverException, self._guest.snapshot,
                          self._context, image_id, self.mock_update_task_state)

        self.mock_update_task_state.assert_called_once_with(
            task_state='image_pending_upload')
        image_service.delete.assert_called_once_with(self._context, image_id)

    @mock.patch('nova.image.glance.get_remote_image_service')
    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    @mock.patch('nova.virt.zvm.hypervisor.Hypervisor.image_delete')
    @mock.patch('nova.virt.zvm.hypervisor.Hypervisor.image_export')
    def test_snapshot_import_fail(self, mock_import, mock_delete,
                                  call, get_image_service):
        image_service = mock.Mock()
        image_id = 'e9ee1562-3ea1-4cb1-9f4c-f2033000eab1'
        get_image_service.return_value = (image_service, image_id)

        mock_import.side_effect = exception.ZVMDriverException(error='error')

        self.assertRaises(exception.ZVMDriverException, self._guest.snapshot,
                          self._context, image_id, self.mock_update_task_state)

        self.mock_update_task_state.assert_called_once_with(
            task_state='image_pending_upload')
        get_image_service.assert_called_with(self._context, image_id)
        call.assert_called_once_with('guest_capture',
                                     self._instance.name, image_id)
        mock_delete.assert_called_once_with(image_id)
        image_service.delete.assert_called_once_with(self._context, image_id)

    @mock.patch.object(six.moves.builtins, 'open')
    @mock.patch('nova.image.glance.get_remote_image_service')
    @mock.patch('nova.virt.zvm.utils.ConnectorClient.call')
    @mock.patch('nova.virt.zvm.hypervisor.Hypervisor.image_delete')
    @mock.patch('nova.virt.zvm.hypervisor.Hypervisor.image_export')
    def test_snapshot_update_fail(self, mock_import, mock_delete, call,
                                  get_image_service, mock_open):
        image_service = mock.Mock()
        image_id = 'e9ee1562-3ea1-4cb1-9f4c-f2033000eab1'
        get_image_service.return_value = (image_service, image_id)
        image_service.update.side_effect = exception.ImageNotAuthorized(
            image_id='dummy')
        image_path = os.path.join(os.path.normpath(
                            CONF.zvm.image_tmp_path), image_id)

        self.assertRaises(exception.ImageNotAuthorized, self._guest.snapshot,
                          self._context, image_id, self.mock_update_task_state)

        mock_open.assert_called_once_with(image_path, 'r')

        get_image_service.assert_called_with(self._context, image_id)
        mock_delete.assert_called_once_with(image_id)
        image_service.delete.assert_called_once_with(self._context, image_id)

        self.mock_update_task_state.assert_has_calls([
            mock.call(task_state='image_pending_upload'),
            mock.call(expected_state='image_pending_upload',
                      task_state='image_uploading')
        ])

        call.assert_called_once_with('guest_capture', self._instance.name,
                                     image_id)
