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
import pwd

from nova.api.metadata import base as instance_metadata
from nova import block_device
from nova.compute import power_state
from nova.i18n import _
from nova.virt import configdrive
from nova.virt import driver
from nova.virt import images
from oslo_config import cfg
from oslo_log import log as logging
from zvmsdk import api as zvm_api
from zvmsdk import dist

from nova_zvm.virt.zvm import configdrive as zvmconfigdrive
from nova_zvm.virt.zvm import const
from nova_zvm.virt.zvm import exception


LOG = logging.getLogger(__name__)
CONF = cfg.CONF

CONF.import_opt('host', 'nova.conf')
CONF.import_opt('instances_path', 'nova.compute.manager')
CONF.import_opt('my_ip', 'nova.conf')


def mapping_power_stat(power_stat):
    """Translate power state to OpenStack defined constants."""
    return const.ZVM_POWER_STAT.get(power_stat, power_state.NOSTATE)


def _volume_in_mapping(mount_device, block_device_info):
    block_device_list = [block_device.strip_dev(vol['mount_device'])
                         for vol in
                         driver.block_device_info_get_mapping(
                             block_device_info)]
    LOG.debug("block_device_list %s", block_device_list)
    return block_device.strip_dev(mount_device) in block_device_list


def is_volume_root(root_device, mountpoint):
    """This judges if the moutpoint equals the root_device."""
    return block_device.strip_dev(mountpoint) == block_device.strip_dev(
                                                                root_device)


def is_boot_from_volume(block_device_info):
    root_mount_device = driver.block_device_info_get_root(block_device_info)
    boot_from_volume = _volume_in_mapping(root_mount_device,
                                          block_device_info)
    return root_mount_device, boot_from_volume


def get_host():
    return ''.join([pwd.getpwuid(os.geteuid()).pw_name, '@', CONF.my_ip])


class PathUtils(object):
    def _get_instances_path(self):
        return os.path.normpath(CONF.instances_path)

    def get_instance_path(self, instance_uuid):
        instance_folder = os.path.join(self._get_instances_path(),
                                       instance_uuid)
        if not os.path.exists(instance_folder):
            LOG.debug("Creating the instance path %s" % instance_folder)
            os.makedirs(instance_folder)
        return instance_folder

    def get_console_log_path(self, os_node, instance_name):
        return os.path.join(self.get_instance_path(os_node, instance_name),
                            "console.log")


class NetworkUtils(object):
    """Utilities for z/VM network operator."""
    pass


class VMUtils(object):
    def __init__(self):
        self._sdk_api = zvm_api.SDKAPI()
        self._dist_manager = dist.LinuxDistManager()
        self._pathutils = PathUtils()
        self._imageutils = ImageUtils()

    # Prepare and create configdrive for instance
    def generate_configdrive(self, context, instance, os_version,
                             network_info, injected_files, admin_password):
        # Create network configuration files
        LOG.debug('Creating network configuration files '
                  'for instance: %s' % instance['name'], instance=instance)

        linuxdist = self._dist_manager.get_linux_dist(os_version)()
        instance_path = self._pathutils.get_instance_path(instance['uuid'])

        files_and_cmds = linuxdist.create_network_configuration_files(
                             instance_path, network_info)
        (net_conf_files, net_conf_cmds) = files_and_cmds
        # Add network configure files to inject_files
        if len(net_conf_files) > 0:
            injected_files.extend(net_conf_files)

        transportfiles = None
        if configdrive.required_by(instance):
            transportfiles = self._create_config_drive(context, instance_path,
                                                       instance,
                                                       injected_files,
                                                       admin_password,
                                                       net_conf_cmds,
                                                       linuxdist)
        return transportfiles

    def _create_config_drive(self, context, instance_path, instance,
                             injected_files, admin_password, commands,
                             linuxdist):
        if CONF.config_drive_format not in ['tgz', 'iso9660']:
            msg = (_("Invalid config drive format %s") %
                   CONF.config_drive_format)
            raise exception.ZVMConfigDriveError(msg=msg)

        LOG.debug('Using config drive', instance=instance)

        extra_md = {}
        if admin_password:
            extra_md['admin_pass'] = admin_password

        udev_settle = linuxdist.get_znetconfig_contents()
        if udev_settle:
            if len(commands) == 0:
                znetconfig = '\n'.join(('#!/bin/bash', udev_settle))
            else:
                znetconfig = '\n'.join(('#!/bin/bash', commands, udev_settle))
            znetconfig += '\nrm -rf /tmp/znetconfig.sh\n'
            # Create a temp file in instance to execute above commands
            net_cmd_file = []
            net_cmd_file.append(('/tmp/znetconfig.sh', znetconfig))  # nosec
            injected_files.extend(net_cmd_file)
            # injected_files.extend(('/tmp/znetconfig.sh', znetconfig))

        inst_md = instance_metadata.InstanceMetadata(instance,
                                                     content=injected_files,
                                                     extra_md=extra_md,
                                                     request_context=context)
        # network_metadata will prevent the hostname of the instance from
        # being set correctly, so clean the value
        inst_md.network_metadata = None

        configdrive_tgz = os.path.join(instance_path, 'cfgdrive.tgz')

        LOG.debug('Creating config drive at %s' % configdrive_tgz,
                  instance=instance)
        with zvmconfigdrive.ZVMConfigDriveBuilder(instance_md=inst_md) as cdb:
            cdb.make_drive(configdrive_tgz)

        return configdrive_tgz


class ImageUtils(object):

    def __init__(self):
        self._pathutils = PathUtils()
        self._sdk_api = zvm_api.SDKAPI()

    def import_spawn_image(self, context, image_href, image_os_version):
        LOG.debug("Downloading the image %s from glance to nova compute "
                  "server" % image_href)

        image_path = os.path.join(os.path.normpath(CONF.zvm_image_tmp_path),
                                  image_href)
        if not os.path.exists(image_path):
            images.fetch(context, image_href, image_path)
        image_url = "file://" + image_path
        image_meta = {'os_version': image_os_version}
        remote_host = get_host()
        self._sdk_api.image_import(image_url, image_meta=image_meta,
                                   remote_host=remote_host)
