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
API, z/VM compute service needs to have a CA file which will be used to verify
that it has the correct authorization to talk with xCAT.

Possible values:
    A CA file name and location in the host that is running compute service.
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
z/VM host that is managed by the compute node.

This is the name of the hypervisor that is managed by the compute service.
The admin should refer to the z/VM system configuration file
SYSTEM_IDENTIFIER statement for the hypervisor name.

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
               help='Default zfcp scsi disk pool'),
    cfg.BoolOpt('zvm_multiple_fcp',
                default=False,
               help='Does use multiple FCP for attaching a volume'),
    cfg.StrOpt('zvm_fcp_list',
               default=None,
               help='Configured fcp list can be used'),
    cfg.StrOpt('zvm_zhcp_fcp_list',
               default=None,
               help='Configured fcp list dedicated to hcp'),
    cfg.BoolOpt('zvm_config_drive_inject_password',
                default=False,
                help='Sets the admin password in the config drive'),
    cfg.StrOpt('zvm_vmrelocate_force',
               default=None,
               help="""
Specify the z/VM Live Guest Relocation FORCE option.

Refer to the z/VM CP Commands and Utilities book for more information on
the z/VM VMRELOCATE command and z/VM Live Guest Relocation,
which backs the live migration feature in the zvm nova driver.

Possible values:
   A string which is one of (ARCHITECTURE, DOMAIN, NONE, or STORAGE)

   ARCHITECTURE:  attempt migration even though hardware architecture
   facilities or z/VM CP features are not available on the destination
   system.

   DOMAIN:  attempt migration even though the destination system is
   not in the z/VM guest's relocation domain.

   STORAGE:  attempt migration even if there appears to be insufficient
   storage resources on the destination system.

   NONE:  No VMRELOCATE FORCE will be used.  Live migration will fail if
   architecture, domain or storage warnings or errors are encountered.

Related values:
    zvm_vmrelocate_immediate
    zvm_vmrelocate_max_total
    zvm_vmrelocate_max_quiesce
"""),
    cfg.StrOpt('zvm_vmrelocate_immediate',
               default='no',
               help="""
Specify the option of live migration timing.

Refer to zVM manual for more info on zVM live migration.

Possible values:
    A string which is one of (YES, NO)

    YES: VMRELOCATE command will do one early pass through virtual machine
         storage and then go directly to the quiesce stage.
    NO:  specifies immediate processing.

Related values:
    zvm_vmrelocate_force
    zvm_vmrelocate_max_total
    zvm_vmrelocate_max_quiesce
"""),
    cfg.StrOpt('zvm_vmrelocate_max_total',
               default='nolimit',
               help="""
Maximum time to wait, in seconds, for live migration to complete.
If this time is exceeded, z/VM will cancel the migration and leave
the z/VM guest running on the source system.

Refer to the z/VM CP Commands and Utilities book for more information
on the z/VM VMRELOCATE command and z/VM Live Guest Relocation,
which backs the live migration feature in the zvm nova driver.

Possible values:
    A positive integer indicating the time in seconds to wait for
    the migration to complete, or "nolimit" to indicate that there is no
    time limit on the total amount of time the system should allow for
    this live migration

Related values:
    zvm_vmrelocate_force
    zvm_vmrelocate_immediate
    zvm_vmrelocate_max_quiesce
"""),
    cfg.StrOpt('zvm_vmrelocate_max_quiesce',
               default='nolimit',
               help="""
Maximum time to wait, in seconds, for the quiesce time of a live migration
to complete.  If this time is exceeded, z/VM will cancel the migration and
leave the z/VM guest running on the source system.
The quiesce time is the time during which the Linux guest will be unresponsive
while its final state is transferred.

Refer to the z/VM CP Commands and Utilities book for more information on the
z/VM VMRELOCATE command and z/VM Live Guest Relocation,
which backs the live migration feature in the zvm nova driver.

Possible values:
    A positive integer indicating the time in seconds to wait for the
    quiesce time to complete, or "nolimit" to indicate that there is no time
    limit on the quiesce time the system should allow for this live migration.

Related values:
    zvm_vmrelocate_force
    zvm_vmrelocate_immediate
    zvm_vmrelocate_max_total
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
The maximum allowed console log size, in kilobytes.

Console logs must be transferred to OpenStack from xCAT, this controls how
large each file can be. A smaller size may mean more calls will be needed
to transfer large consoles, which may not be desirable for performance reasons.
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
statement.

Possible values:
    A 1-8 character string.
"""),
    cfg.StrOpt('zvm_default_admin_userid',
               default='',
               help="""
Default LOGONBY userid for the cloud.

Blank-delimited list of z/VM userids allowed to logon using the BY keyword
to the guests created by zVM openstack solution, compatible with the LOGONBY
keyword of the user directory statement.  This value is only used when a guest
is created. If you change this value existing guests' directory entries are not
automatically updated with the new value.
When an ESM is installed, this parameter only governs when the ESM
defers to CP's processing.

Usage note:
    The default is an empty string (''). When the string is empty, you can't
    log on your cloud created instance using the 3270 protocol; When a
    non-empty string is provied, you can use LOGONBY xxx command to log on the
    openstack created guest using the corresponding admin userid's password.

    see zVM CP Planning and Administration for additional information.

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
    Currently, 'G' is the recommended value.
    Consult with your z/VM administrator for more information.
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
