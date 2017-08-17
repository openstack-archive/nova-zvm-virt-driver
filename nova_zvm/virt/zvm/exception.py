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


from nova import exception
from nova.i18n import _


class ZVMBaseException(exception.NovaException):
    """Base z/VM exception."""
    pass


class ZVMDriverError(ZVMBaseException):
    msg_fmt = _('z/VM driver error: %(msg)s')


class ZVMVolumeError(ZVMBaseException):
    msg_fmt = _('Volume error: %(msg)s')


class ZVMImageError(ZVMBaseException):
    msg_fmt = _("Image error: %(msg)s")


class ZVMNetworkError(ZVMBaseException):
    msg_fmt = _("z/VM network error: %(msg)s")


class ZVMConfigDriveError(ZVMBaseException):
    msg_fmt = _('Create configure drive failed: %(msg)s')


class ZVMRetryException(ZVMBaseException):
    pass
