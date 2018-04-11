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
from oslo_log import log as logging
import six
import six.moves.urllib.parse as urlparse
from zvmconnector import connector

from nova.api.metadata import base as instance_metadata
from nova import conf
from nova import exception
from nova.i18n import _
from nova.virt import configdrive
from nova.virt.zvm import configdrive as zvmconfigdrive


CONF = conf.CONF
LOG = logging.getLogger(__name__)


class ConnectorClient(object):
    """Request handler to zVM cloud connector"""

    def __init__(self, zcc_url, ca_file=None):
        _url = urlparse.urlparse(zcc_url)

        _ssl_enabled = False

        if _url.scheme == 'https':
            _ssl_enabled = True

        if _ssl_enabled and ca_file:
            self._conn = connector.ZVMConnector(_url.hostname, _url.port,
                                                ssl_enabled=_ssl_enabled,
                                                verify=ca_file)
        else:
            self._conn = connector.ZVMConnector(_url.hostname, _url.port,
                                                ssl_enabled=_ssl_enabled,
                                                verify=False)

    def call(self, func_name, *args, **kwargs):
        results = self._conn.send_request(func_name, *args, **kwargs)

        if results['overallRC'] != 0:
            LOG.debug("zVM Cloud Connector request %(api)s failed with "
               "parameters: %(args)s %(kwargs)s .  Results: %(results)s",
               {'api': func_name, 'args': six.text_type(args),
                'kwargs': six.text_type(kwargs),
                'results': six.text_type(results)})
            raise exception.ZVMDriverException("zVM Cloud Connector request "
                                               "failed", results=results)

        return results['output']


def _get_instances_path():
    return os.path.normpath(CONF.instances_path)


def get_instance_path(instance_uuid):
    instance_folder = os.path.join(_get_instances_path(),
                                   instance_uuid)
    if not os.path.exists(instance_folder):
        LOG.debug("Creating the instance path %s", instance_folder)
        os.makedirs(instance_folder)
    return instance_folder


def _create_config_drive(context, instance_path, instance,
                         injected_files, admin_password):
    if CONF.config_drive_format not in ['iso9660']:
        msg = (_("Invalid config drive format %s") %
               CONF.config_drive_format)
        LOG.debug(msg)
        raise exception.ConfigDriveUnsupportedFormat(
                        format=CONF.config_drive_format)

    LOG.debug('Using config drive', instance=instance)

    extra_md = {}
    if admin_password:
        extra_md['admin_pass'] = admin_password

    inst_md = instance_metadata.InstanceMetadata(instance,
                                                 content=injected_files,
                                                 extra_md=extra_md,
                                                 request_context=context)
    # network_metadata will prevent the hostname of the instance from
    # being set correctly, so clean the value
    inst_md.network_metadata = None

    configdrive_tgz = os.path.join(instance_path, 'cfgdrive.tgz')

    LOG.debug('Creating config drive at %s', configdrive_tgz,
              instance=instance)
    with zvmconfigdrive.ZVMConfigDriveBuilder(instance_md=inst_md) as cdb:
        cdb.make_drive(configdrive_tgz)

    return configdrive_tgz


# Prepare and create configdrive for instance
def generate_configdrive(context, instance, injected_files,
                         admin_password):
    # Create network configuration files
    LOG.debug('Creating network configuration files '
              'for instance: %s', instance.name, instance=instance)

    instance_path = get_instance_path(instance.uuid)

    transportfiles = None
    if configdrive.required_by(instance):
        transportfiles = _create_config_drive(context, instance_path,
                                              instance,
                                              injected_files,
                                              admin_password)
    return transportfiles


def clean_up_file(filepath):
    if os.path.exists(filepath):
        os.remove(filepath)
