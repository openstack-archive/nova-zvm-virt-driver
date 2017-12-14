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


"""Test suite for ZVM configure drive."""

import os

from nova import exception
from nova import test
from oslo_utils import fileutils

from nova_zvm.virt.zvm import conf
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
        self._file_name = self._file_path + '/cfgdrive.tgz'
        self.inst_md = FakeInstMeta()

    def test_create_configdrive_tgz(self):
        self._file_path = CONF.tempdir
        fileutils.ensure_tree(self._file_path)
        try:
            with zvmconfigdrive.ZVMConfigDriveBuilder(
                                            instance_md=self.inst_md) as c:
                c.make_drive(self._file_name)

            self.assertTrue(os.path.exists(self._file_name))

        finally:
            fileutils.remove_path_on_error(self._file_path)

    def test_make_drive_unknown_format(self):
        self.flags(config_drive_format='vfat')
        try:
            with zvmconfigdrive.ZVMConfigDriveBuilder(
                                            instance_md=self.inst_md) as c:
                self.assertRaises(exception.ConfigDriveUnknownFormat,
                                  c.make_drive, self._file_name)
        finally:
            fileutils.remove_path_on_error(self._file_path)
