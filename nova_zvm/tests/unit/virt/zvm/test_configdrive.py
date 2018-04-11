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
import shutil

from oslo_utils import fileutils

from nova import conf
from nova import test
from nova_zvm.virt.zvm import configdrive as zvmconfigdrive


CONF = conf.CONF


class FakeInstMeta(object):

    def metadata_for_config_drive(self):
        return [('openstack', 'data1'), ('ec2', 'data2')]


class ZVMConfigDriveTestCase(test.NoDBTestCase):
    def setUp(self):
        super(ZVMConfigDriveTestCase, self).setUp()
        self.flags(config_drive_format='iso9660',
                   tempdir='/tmp/os')

        self._file_path = CONF.tempdir
        self.inst_md = FakeInstMeta()

    def tearDown(self):
        super(ZVMConfigDriveTestCase, self).tearDown()
        shutil.rmtree(self._file_path)

    def test_create_configdrive_tgz(self):
        fileutils.ensure_tree(self._file_path)
        self._file_name = self._file_path + '/cfgdrive.tgz'

        with zvmconfigdrive.ZVMConfigDriveBuilder(
                                        instance_md=self.inst_md) as c:
            c.make_drive(self._file_name)

            self.assertTrue(os.path.exists(self._file_name))
