# Copyright 2016 IBM Corp.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import itertools

from nova_zvm.virt.zvm import conf as zvm_conf


def list_opts():
    return [
        # Actually it should be [zvm], but for backward compatible issue,
        # we keep this into DEFAULT.
        ('DEFAULT',
         itertools.chain(
             zvm_conf.zvm_image_opts,
             zvm_conf.zvm_opts,
             zvm_conf.zvm_user_opts,
         )),
    ]
