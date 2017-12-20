# Copyright 2016 IBM Corp.
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

from oslo_config import cfg

zvm_opts = [
    cfg.URIOpt('zvm_cloud_connector_url',
               help="""
URL to be used to communicate with z/VM Cloud Connector.
Example: https://10.10.10.1:8080.
"""),
    cfg.StrOpt('zvm_image_tmp_path',
               default='/var/lib/nova/images',
               help="""
The path at which images will be stored (snapshot, deploy, etc).

The image used to deploy or image captured from instance need to be
stored in local disk of compute node host. This configuration identifies
the directory location.

Possible values:
    A path in host that running compute service.
"""),
    cfg.IntOpt('zvm_reachable_timeout',
               default=300,
               help="""
Timeout (seconds) to wait for an instance to start.

The z/VM driver relies on SSH between the instance and xCAT for communication.
So after an instance is logged on, it must have enough time to start SSH
communication. The driver will keep rechecking SSH communication to the
instance for this timeout. If it can not SSH to the instance, it will notify
the user that starting the instance failed and put the instance in ERROR state.
The underlying z/VM guest will then be deleted.

Possible Values:
    Any positive integer. Recommended to be at least 300 seconds (5 minutes),
    but it will vary depending on instance and system load.
    A value of 0 is used for debug. In this case the underlying z/VM guest
    will not be deleted when the instance is marked in ERROR state.
"""),
    cfg.StrOpt('zvm_ca_file',
               default=None,
               help="""
CA certificate file to be verified in httpd server

A string, it must be a path to a CA bundle to use.
"""),
    ]

CONF = cfg.CONF
CONF.register_opts(zvm_opts)
