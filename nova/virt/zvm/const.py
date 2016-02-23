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


from nova.compute import power_state


HYPERVISOR_TYPE = 'zvm'
ARCHITECTURE = 's390x'
ALLOWED_VM_TYPE = 'zLinux'
XCAT_MGT = 'zvm'

XCAT_RINV_HOST_KEYWORDS = {
    "zvm_host": "z/VM Host:",
    "zhcp": "zHCP:",
    "cec_vendor": "CEC Vendor:",
    "cec_model": "CEC Model:",
    "hypervisor_os": "Hypervisor OS:",
    "hypervisor_name": "Hypervisor Name:",
    "architecture": "Architecture:",
    "lpar_cpu_total": "LPAR CPU Total:",
    "lpar_cpu_used": "LPAR CPU Used:",
    "lpar_memory_total": "LPAR Memory Total:",
    "lpar_memory_used": "LPAR Memory Used:",
    "lpar_memory_offline": "LPAR Memory Offline:",
    "ipl_time": "IPL Time:",
    }

XCAT_DISKPOOL_KEYWORDS = {
    "disk_total": "Total:",
    "disk_used": "Used:",
    "disk_available": "Free:",
    }

XCAT_RESPONSE_KEYS = ('info', 'data', 'node', 'errorcode', 'error')

ZVM_POWER_STAT = {
    'on': power_state.RUNNING,
    'off': power_state.SHUTDOWN,
    }

ZVM_DEFAULT_ROOT_DISK = "dasda"
ZVM_DEFAULT_SECOND_DISK = "dasdb"
ZVM_DEFAULT_ROOT_VOLUME = "sda"
ZVM_DEFAULT_SECOND_VOLUME = "sdb"
ZVM_DEFAULT_THIRD_VOLUME = "sdc"
ZVM_DEFAULT_LAST_VOLUME = "sdz"

DEFAULT_EPH_DISK_FMT = "ext3"
DISK_FUNC_NAME = "setupDisk"

ZVM_DEFAULT_FCP_ID = 'auto'

ZVM_DEFAULT_NIC_VDEV = '1000'

ZVM_IMAGE_SIZE_MAX = 10

XCAT_SUPPORT_CHVM_SMCLI_VERSION = '2.8.3.8'
