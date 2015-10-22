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

import os
import tarfile

from oslo_config import cfg
from oslo_log import log as logging

from nova import exception
from nova import utils
from nova.virt import configdrive
from nova.virt.zvm import utils as zvmutils


LOG = logging.getLogger(__name__)


CONF = cfg.CONF


class ZVMConfigDriveBuilder(configdrive.ConfigDriveBuilder):
    """Enable ConfigDrive to make tgz package."""

    def __init__(self, instance_md):
        super(ZVMConfigDriveBuilder, self).__init__(instance_md)

    def make_drive(self, path):
        """Make the config drive.

        :param path: the path to place the config drive image at
        :raises ProcessExecuteError if a helper process has failed.

        """
        if CONF.config_drive_format in ['tgz', 'iso9660']:
            self._make_tgz(path)
        else:
            raise exception.ConfigDriveUnknownFormat(
                format=CONF.config_drive_format)

    def _make_tgz(self, path):
        try:
            olddir = os.getcwd()
        except OSError:
            olddir = CONF.state_path

        with utils.tempdir() as tmpdir:
            self._write_md_files(tmpdir)
            tar = tarfile.open(path, "w:gz")
            os.chdir(tmpdir)
            tar.add("openstack")
            tar.add("ec2")
            try:
                os.chdir(olddir)
            except Exception as e:
                emsg = zvmutils.format_exception_msg(e)
                LOG.debug('exception in _make_tgz %s', emsg)

            tar.close()
