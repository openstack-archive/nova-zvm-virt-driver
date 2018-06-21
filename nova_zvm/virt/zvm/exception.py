# Copyright 2018 IBM Corp.
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


class ZVMDriverException(exception.NovaException):
    msg_fmt = _("ZVM Driver has error: %(error)s")


class ZVMConnectorError(ZVMDriverException):
    msg_fmt = _("zVM Cloud Connector request failed: %(results)s")

    def __init__(self, message=None, **kwargs):
        """Exception for zVM ConnectorClient calls.

        :param results: The object returned from ZVMConnector.send_request.
        """
        super(ZVMConnectorError, self).__init__(message=message, **kwargs)

        results = kwargs.get('results', {})
        self.overallRC = results.get('overallRC')
        self.rc = results.get('rc')
        self.rs = results.get('rs')
        self.errmsg = results.get('errmsg')
