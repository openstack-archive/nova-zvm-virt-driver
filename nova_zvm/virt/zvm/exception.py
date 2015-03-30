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


from nova import exception
from nova.i18n import _


class ZVMBaseException(exception.NovaException):
    """Base z/VM exception."""
    pass


class ZVMDriverError(ZVMBaseException):
    msg_fmt = _('z/VM driver error: %(msg)s')


class ZVMXCATRequestFailed(ZVMBaseException):
    msg_fmt = _('Request to xCAT server %(xcatserver)s failed: %(msg)s')


class ZVMInvalidXCATResponseDataError(ZVMBaseException):
    msg_fmt = _('Invalid data returned from xCAT: %(msg)s')


class ZVMXCATInternalError(ZVMBaseException):
    msg_fmt = _('Error returned from xCAT: %(msg)s')


class ZVMVolumeError(ZVMBaseException):
    msg_fmt = _('Volume error: %(msg)s')


class ZVMImageError(ZVMBaseException):
    msg_fmt = _("Image error: %(msg)s")


class ZVMGetImageFromXCATFailed(ZVMBaseException):
    msg_fmt = _('Get image from xCAT failed: %(msg)s')


class ZVMNetworkError(ZVMBaseException):
    msg_fmt = _("z/VM network error: %(msg)s")


class ZVMXCATXdshFailed(ZVMBaseException):
    msg_fmt = _('Execute xCAT xdsh command failed: %(msg)s')


class ZVMXCATCreateNodeFailed(ZVMBaseException):
    msg_fmt = _('Create xCAT node %(node)s failed: %(msg)s')


class ZVMXCATCreateUserIdFailed(ZVMBaseException):
    msg_fmt = _('Create xCAT user id %(instance)s failed: %(msg)s')


class ZVMXCATUpdateNodeFailed(ZVMBaseException):
    msg_fmt = _('Update node %(node)s info failed: %(msg)s')


class ZVMXCATDeployNodeFailed(ZVMBaseException):
    msg_fmt = _('Deploy image on node %(node)s failed: %(msg)s')


class ZVMConfigDriveError(ZVMBaseException):
    msg_fmt = _('Create configure drive failed: %(msg)s')
