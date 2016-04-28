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
               help='Host name or IP address of xCAT management_node'),
    cfg.StrOpt('zvm_xcat_username',
               default=None,
               help='XCAT username'),
    cfg.StrOpt('zvm_xcat_password',
               default=None,
               secret=True,
               help='Password of the xCAT user'),
    cfg.StrOpt('zvm_diskpool',
               default=None,
               help="""
zVM disk pool for ephemeral disks.

The volume group name in your z/VM system from which xCAT will allocate a disk
for new servers and it must be pre-defined in zVM a dollar sign ($) is
reserved and it should not used in the diskpool name.

Related:
    zvm_diskpool_type
"""),
    cfg.StrOpt('zvm_diskpool_type',
               default='ECKD',
               help="""
Disk type of the disk in the disk pool.

Only one type is supported currently, it should be the type of the
<zvm_diskpool> created in zVM.

Related:
    zvm_diskpool

Possible values:
    A string, either ECKD or FBA.
"""),
    cfg.StrOpt('zvm_host',
               default=None,
               help='Z/VM host that managed by xCAT MN.'),
    cfg.StrOpt('zvm_xcat_group',
               default='all',
               help='XCAT group for OpenStack'),
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
               help='Timeout(seconds) when start an instance.'),
    cfg.IntOpt('zvm_xcat_connection_timeout',
               default=3600,
               help='XCAT connection read timeout(seconds)'),
    cfg.IntOpt('zvm_console_log_size',
               default=100,
               help='Max console log size(kilobyte) get from xCAT'),
    ]

zvm_user_opts = [
    cfg.StrOpt('zvm_user_profile',
               default=None,
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
               help='Virtual device number for ephemeral root disk'),
    cfg.StrOpt('zvm_default_nic_vdev',
               default='1000',
               help='Virtual device number for default NIC address'),
    cfg.StrOpt('zvm_user_adde_vdev',
               default='0101',
               help='Virtual device number for additional ephemeral disk'),
    cfg.StrOpt('zvm_user_volume_vdev',
               default='0200',
               help='Virtual device number for persistent volume, '
                    'if there are more then one volumes, will use next vdev'),
    ]

zvm_image_opts = [
    cfg.StrOpt('zvm_image_tmp_path',
               default='/var/lib/nova/images',
               help='The path to store the z/VM image files'),
    cfg.StrOpt('zvm_default_ephemeral_mntdir',
               default='/mnt/ephemeral',
               help='The path to which the ephemeral disk be mounted'),
    cfg.StrOpt('zvm_image_default_password',
               default='rootpass',
               secret=True,
               help='Default os root password for a new created vm'),
    cfg.IntOpt('xcat_image_clean_period',
               default=30,
               help='The period(days) to clean up an image that not be used '
                     'for deploy in one xCAT MN within the defined time'),
    cfg.IntOpt('xcat_free_space_threshold',
               default=50,
               help='The threshold for xCAT free space, if snapshot or spawn '
                     'check xCAT free space not enough for its image '
                     'operations, it will prune image to meet the threshold'),
    cfg.StrOpt('zvm_xcat_master',
               default=None,
               help='The xCAT MM node name'),
    cfg.StrOpt('zvm_image_compression_level',
               default=None,
               help='The level of gzip compression used when capturing disk'),
    ]

CONF = cfg.CONF
CONF.register_opts(zvm_opts)
CONF.register_opts(zvm_user_opts)
CONF.register_opts(zvm_image_opts)
