# vim: tabstop=4 shiftwidth=4 softtabstop=4

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

"""A connection to an IBM z/VM Virtualization system.

Generally, OpenStack z/VM virt driver will call xCat REST API to operate
to z/VM hypervisors.xCat has a control point(a virtual machine) in z/VM
system, which enables xCat management node to control the z/VM system.
OpenStack z/VM driver will communicate with xCat management node through
xCat REST API. Thus OpenStack can operate to z/VM system indirectly.

"""


from nova_zvm.virt.zvm import driver


ZVMDriver = driver.ZVMDriver
