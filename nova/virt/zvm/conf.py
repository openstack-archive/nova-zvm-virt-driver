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
CA file for https connection to xCAT REST API.

When HTTPS protocol is used to communicate between z/VM driver and xCAT REST
API, z/VM driver need to have a CA file which will be used to verify xCAT is
the one z/VM driver to connect to.

Possible values:
    A CA file name and location in the host that running compute service.
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
z/VM host that managed by the compute node.

This is the name of the hypervisor that is managed by the compute service.
Admin need to set this name by refering to the z/VM system configuration
file.

Possible values:
    A 1-8 character string, matching the z/VM system name this
    compute service is managing.
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
instances. Each instance needs at least one FCP in order to attach a volume
to itself and the number of FCP depends on the path connect between host and
storage. Those FCPs should be available and online before OpenStack can use
them. OpenStack will not check their status but use them directly, so if they
are not ready, errors may be returned. Contact your z/VM system
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
specified with a semicolon connecting them. The purpose of this param
is to ensure links between z/VM host and storage are pre-setup so further
attach detach actions of virtual machines on same z/VM can proceed.

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
The maximum console log size, in kilobytes, allowed.

Console logs must be transferred to OpenStack from z/VM side,
so this controls how large each transferred console can be.

Possible values:
    Any positive integer, recommended to be at least 100 KB to
    avoid unnecessary calls between z/VM and OpenStack.
"""),
    ]

zvm_user_opts = [
    cfg.StrOpt('zvm_user_profile',
               default='osdflt',
               help="""
PROFILE name to use when creating a z/VM guest.

When nova deploys an instance on z/VM, it can include some common statements
from a PROFILE definition.
This PROFILE must already be included in your z/VM user directory.

Possible values:
    An 8 character name of a PROFILE that is already defined in the z/VM
    user directory.
"""),
    cfg.StrOpt('zvm_user_default_password',
               default='dfltpass',
               secret=True,
               help="""
Default password for a new created z/VM user.

This is the password for any z/VM user IDs created when OpenStack deploys new
virtual servers (also called nova instances), defined in the USER directory
statement. The default value is dfltpass. You can change it as needed.

Possible values:
    A 1-8 character string.
"""),
    cfg.StrOpt('zvm_user_default_privilege',
               default='G',
               help="""
Privilege class for the z/VM guest.

The privilege class controls which z/VM commands the guest can issue.
Classes A-G are defined by default, or the administrator can define
their own using characters A-Z, 1-6.

Possible value:
    In opnestack solution, 'G' is the only recommended value.
    Consult with your zVM admin for more info.
"""),
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

Copies of Glance images are kept in the xCAT MN to make deploys faster.
Unused images are purged to reclaim disk space. If an image has been purged
from xCAT, the next deploy will take slightly longer as it must be copied
from OpenStack into xCAT.

Possible values:
    Any positive integer, recommended to be at least 30 (1 month).
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

A snapshotted image will consume disk space on xCAT MN host and the OpenStack
compute host. To save disk space the image should be compressed.
The zvm driver uses gzip to compress the image. gzip has a set of different
levels depending on the speed and quality of compression.
For more information, please refer to the -N option of the gzip command.

Possible values:
    An integer between 0 and 9, where 0 is no compression and 9 is the best,
    but slowest compression. A value of "None" will result in the default
    compression level, which is currently '6' for gzip.
"""),
    ]

CONF = cfg.CONF
CONF.register_opts(zvm_opts)
CONF.register_opts(zvm_user_opts)
CONF.register_opts(zvm_image_opts)
