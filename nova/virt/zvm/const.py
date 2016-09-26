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

ZVM_IMAGE_SIZE_MAX = 10

# This is the version that our plugin can work ,if xcat version
# is lower than this version, we will warn user and prevent
# compute service to start up.
# Will bump this version time to time for each release.
XCAT_MINIMUM_VERSION = '2.8.3.7'


# It means we introduced 'version' concept at 2.8.3.7
# later on, any new features especially backward incompatible
# change need a new version such as
# Support_xxxx = 'x.x.x.x', then we will compare whether
# The XCAT we are using has higher or lower version than x.x.x.x
# and do different things according to the version
# we might INFO in log if version is lower than XCAT_INIT_VERSION
XCAT_INIT_VERSION = '2.8.3.7'

# From 2.8.3.7 version, openstack will only send 'nozip' format
# to xcat, so before that, a tgz format will be send and processed
# while >= this version, a nozip flag along with tar format is sent.
# xcat was bumped to this version at 2015.08.06, so version lower than
# it should use zip format instead.
XCAT_BUNDLE_USE_NOZIP = '2.8.3.7'

XCAT_SUPPORT_CHVM_SMCLI_VERSION = '2.8.3.8'

# e0a7bc3502e8635bf7a2954220a6406f563f3a1f added this
# support add --ipl as param to mkvm call
XCAT_MKVM_SUPPORT_IPL = '2.8.3.11'

# I1a6ff024609e50f3a20a4bac2d86f94f7152e3cf xcat change added this
# rinv now can use 'cpumempowerstat' option
XCAT_RINV_SUPPORT_CPUMEMPOWERSTAT = '2.8.3.13'

# xCAT version that supports collection of diagnostics for failed deployments
XCAT_SUPPORT_COLLECT_DIAGNOSTICS_DEPLOYFAILED = '2.8.3.16'

# Reasons for collecting diagnostics.
# xCAT may adjust diagnostic contents based on the reason passed in.
DIAGNOSTICS_RSN_DEPLOYMENT_TIMEOUT = 'OpenStackDeploymentTimeout'
