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
    cfg.StrOpt('zvm_xcat_server',
               default=None,
               help="""
Host name or IP address of xCAT management_node.

In order for the nova compute service to manage zVM, it needs to talk to an
xcat management node. This property specifies the ip address or host name
(which can be known from DNS of compute host) of the xcat MN server.

Possible values:
    IP address(ipaddr) or host name(string)
"""),
    cfg.StrOpt('zvm_xcat_username',
               default=None,
               help="""
The xCAT user name for the REST API calls.

This is the user who will be authenticated by xCAT when the REST
API call is made. Usually the user should have admin access to the resource
being managed by xcat.

Related:
    zvm_xcat_password
"""),
    cfg.StrOpt('zvm_xcat_password',
               default=None,
               secret=True,
               help="""
The xCAT password for the REST API calls

This is the password which will be authenticated by xCAT when
the REST API call is made.

Related:
    zvm_xcat_username
"""),
    cfg.StrOpt('zvm_xcat_master',
               default=None,
               help="""
The xCAT management node name from the xCAT database.

The nova zvm driver sometimes needs to interact with the xCAT management node
itself, for example to query the free disk space on the node.
 In that case it needs this node name for the REST API call.

Possible values:
    A string specifying the name of the xCAT management node.
    This name should come from the xCAT node database.
"""),
    cfg.StrOpt('zvm_xcat_ca_file',
               default=None,
               help="""
CA file for https connection to xcat.

When https is used to communicate between zvm driver and xcat,
zvm driver need to have a CA file so that certificates will
be used to verify xcat is the one zvm plugin to connect to.
see https://bugs.launchpad.net/ossn/+bug/1188189 for additional info.

Possible values:
    A file location in the host that running compute service.
"""),
    cfg.StrOpt('zvm_diskpool',
               default=None,
               help="""
zVM disk pool for ephemeral disks.

The volume group name from your directory manager on your z/VM system,
which will be used for ephemeral disks for new instances.
A dollar sign ($) is not allowed in the name.

Related:
    zvm_diskpool_type
"""),
    cfg.StrOpt('zvm_diskpool_type',
               default='ECKD',
               help="""
Disk type of the disk in the disk pool.

The disks in the disk pool must all be the same type (ECKD or FBA).

Related:
    zvm_diskpool

Possible values:
    A string, either ECKD or FBA.
"""),
    cfg.StrOpt('zvm_host',
               default=None,
               help="""
Z/VM host that managed by xCAT MN.

This variable identify the hypervisor name that managed by the compute
service running on. It is required to be same to the system name of the
z/VM in the z/VM system configuration file.

Possible values:
    A string, which is the system name of the z/VM that this compute
    service managing.
"""),
    cfg.StrOpt('zvm_xcat_group',
               default='all',
               help="""
XCAT group for OpenStack.

There is a 'group' field in xCAT side and it can divide nodes into
different group and let GUI to select a group of nodes to display.

Possible values:
    A string, only 'all' is valid value.
"""),
    cfg.StrOpt('zvm_scsi_pool',
               default='xcatzfcp',
               help="""
The name of an xCAT SCSI pool file.

This is the name of the pool file in xCAT that will be created to maintain
the list of volumes available and allocated to deployed servers.

Possible values:
    A string which contains a valid xCAT file name.
    This can be an existing pool, or one that is to be created.
"""),
    cfg.BoolOpt('zvm_multiple_fcp',
                default=False,
                help="""
Defines whether to use host side multipath.

Defines whether to use host side multipath (which supports two paths to
a persistent disk) when attaching a persistent disk, so that if one path
fails, the instance can access the disk via the other path.

To use the host side multipath feature, at least two sets of FCP devices
corresponding to different CHPIDs must be configured in the zvm_fcp_list
property.

Related:
    zvm_fcp_list
    zvm_zhcp_fcp_list

Possible values:
    True or False.
"""),
    cfg.StrOpt('zvm_fcp_list',
               default=None,
               help="""
The list of FCPs used by virtual server instances.

Required only if persistent disks are to be attached to virtual server
instances. Each instance needs FCP in order to attach a volume to itself
and the number of FCP depends on the path connect between host and storage.
Those FCPs should be available and online before OpenStack can use them.
OpenStack will not check their status but use them directly, so if they are
not ready, errors may be returned. Contact your z/VM system
administrator if you do not know which FCPs you can use.

Related:
    zvm_multiple_fcp
    zvm_zhcp_fcp_list

Possible values:
    A semicolon delimited string of either single FCP numbers or
    ranges, e.g., 1f0e;2f02-2f1f;3f00.
"""),
    cfg.StrOpt('zvm_zhcp_fcp_list',
               default=None,
               help="""
The list of FCPs used only by the xCAT ZHCP node.

The FCP addresses may be specified as either an individual address or a
range of addresses connected with a hyphen. Multiple values are
specified with a semicolon connecting them.

The FCP addresses must be different from the ones specified for the
zvm_fcp_list. Any FCPs that exist in both zvm_fcp_list and zvm_zhcp_fcp_list
will lead to errors. Contact your z/VM system administrator if you do
not know which FCPs you can use.

Related:
    zvm_fcp_list
    zvm_multiple_fcp

Possible values:
    A string with hypon and semicolon, e.g 1f0e;2f02-2f1f;3f00.

"""),
    cfg.BoolOpt('zvm_config_drive_inject_password',
                default=False,
                help='Sets the admin password in the config drive'),
    cfg.StrOpt('zvm_vmrelocate_force',
               default=None,
               help='Force can be: (ARCHITECTURE) attempt relocation even '
                    'though hardware architecture facilities or CP features '
                    'are not available on destination system, '
                    '(DOMAIN) attempt relocation even though VM would be '
                    'moved outside of its domain, '
                    'or (STORAGE) relocation should proceed even if CP '
                    'determines that there are insufficient storage '
                    'resources on destination system.'),
    cfg.StrOpt('zvm_vmrelocate_immediate',
               default='yes',
               help='Immediate can be: (YES) VMRELOCATE command will do '
                    'one early pass through virtual machine storage and '
                    'then go directly to the quiesce stage, '
                    'or (NO) specifies immediate processing.'),
    cfg.StrOpt('zvm_vmrelocate_max_total',
               default='nolimit',
               help='Maximum wait time(seconds) for relocation to complete'),
    cfg.StrOpt('zvm_vmrelocate_max_quiesce',
               default='nolimit',
               help='Maximum quiesce time(seconds) a VM may be stopped '
                    'during a relocation attempt'),
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
    cfg.IntOpt('zvm_xcat_connection_timeout',
               default=3600,
               help="""
Timeout (seconds) for an xCAT HTTP request.

The z/VM driver communicates with xCAT via REST APIs.  This value states the
maximum amount of time the z/VM driver will wait before timing out a REST API
call. Some actions, like copying an image to the OpenStack compute node,
may take a long time, so set this to the maximum time these long
running tasks may take.

Possible Values:
    Any positive integer.
    Recommended to be larger than 3600 (1 hour), depending on the size of
    your images.
"""),
    cfg.IntOpt('zvm_console_log_size',
               default=100,
               help="""
Max console log size(kilobyte) get from xCAT
"""),
    ]

zvm_user_opts = [
    cfg.StrOpt('zvm_user_profile',
               default='osdflt',
               help='User profile for creating a z/VM userid'),
    cfg.StrOpt('zvm_user_default_password',
               default='dfltpass',
               secret=True,
               help='Default password for a new created z/VM user'),
    cfg.StrOpt('zvm_user_default_privilege',
               default='g',
               help='Default privilege level for a new created z/VM user'),
    cfg.StrOpt('zvm_user_root_vdev',
               default='0100',
               help="""
Virtual device number for root disk.

When nova deploys an instance, it creates a root disk and several ephemeral
or persistent disks. This value is the virtual device number of the root
disk. If the root disk is a cinder volume instead, this value does not apply.

Possible values:
    An integer value in hex format, between 0 and 65536 (x'FFFF').
    It should not conflict with other device numbers in the z/VM guest's
    configuration, for example device numbers of the NICs or ephemeral or
    persistent disks.

Sample root disk in user directory:
    MDISK 0100 <disktype> <start> <end> <volumelabel> <readwrite>

Related values:
    zvm_default_nic_vdev
    zvm_user_adde_vdev
"""),
    cfg.StrOpt('zvm_default_nic_vdev',
               default='1000',
               help="""
Virtual device number for default NIC address.

This value is the first NIC virtual device number,
each NIC needs 3 numbers for control/read/write, so by default
the first NIC's address is 1000, the second one is 1003 etc.

Possible values:
    An integer value in hex format, between 0 and 65536 (x'FFFF').
    It should not conflict with other device numbers in the z/VM guest's
    configuration, for example device numbers of the root or ephemeral or
    persistent disks.

Sample NIC definitions in the z/VM user directory:
    NICDEF 1000 TYPE QDIO LAN SYSTEM <vswitch1> MACID <macid1>
    NICDEF 1003 TYPE QDIO LAN SYSTEM <vswitch2> MACID <macid2>

Related values:
    zvm_user_root_vdev
    zvm_user_adde_vdev
"""),
    cfg.StrOpt('zvm_user_adde_vdev',
               default='0101',
               help="""
Virtual device number for additional ephemeral disk.

Nova allows a user to deploy an instance with one or more ephemeral disks.
This value is the virtual device number of the first ephemeral disk.
Other ephemeral disks will take subsequent numbers.

Possible values:
    An integer value in hex format, between 0 and 65536 (x'FFFF').
    It should not conflict with other device numbers in the z/VM guest's
    configuration, for example device numbers of the root disk or NICs.

Sample ephemeral disk definitions in the z/VM user directory:
    MDISK 0101 <disktype1> <start1> <end1> <volumelabel1> <readwrite1>
    MDISK 0102 <disktype2> <start2> <end2> <volumelabel2> <readwrite2>

Related values:
    zvm_user_root_vdev
    vm_default_nic_vdev
"""),
    ]

zvm_image_opts = [
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
    cfg.StrOpt('zvm_default_ephemeral_mntdir',
               default='/mnt/ephemeral',
               help='The path to which the ephemeral disk be mounted'),
    cfg.StrOpt('zvm_image_default_password',
               default='rootpass',
               secret=True,
               help='Default os root password for a new created vm'),
    cfg.IntOpt('xcat_image_clean_period',
               default=30,
               help="""
Number of days an unused xCAT image will be retained before it is purged.

The period(days) to clean up an image that not be used for deploy in one
xCAT MN within the defined time.

Possible values:
    Any positive integer. Recommended value is 30 or higher to keep images
    in xcat so the deploy don't need to download from openstack every time.
"""),
    cfg.IntOpt('xcat_free_space_threshold',
               default=50,
               help='The threshold for xCAT free space, if snapshot or spawn '
                     'check xCAT free space not enough for its image '
                     'operations, it will prune image to meet the threshold'),
    cfg.StrOpt('zvm_image_compression_level',
               default=None,
               help="""
The level of gzip compression used when capturing disk.

The snapshot image usually will consume disk space on xCAT MN host and
compute host, in order to save disk space the image can be compressed.
zvm driver use gzip to compress the image and gzip provided a set of compress
zip level. For more information, please refer to zip level of gzip command.

Possible values:
    An integer between 0 and 9, set to 0 means no zip and set to None will
    make zvm driver pick '6' which is default compress level of gzip.
"""),
    ]

CONF = cfg.CONF
CONF.register_opts(zvm_opts)
CONF.register_opts(zvm_user_opts)
CONF.register_opts(zvm_image_opts)
