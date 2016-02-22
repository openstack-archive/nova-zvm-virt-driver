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


from oslo_config import cfg
from oslo_log import log as logging

from nova.i18n import _
from nova.virt.zvm import exception
from nova.virt.zvm import utils as zvmutils


LOG = logging.getLogger(__name__)

CONF = cfg.CONF

NetworkUtils = zvmutils.NetworkUtils()


class NetworkOperator(object):
    """Configuration check and manage MAC address."""

    def __init__(self):
        self._xcat_url = zvmutils.XCATUrl()

    def add_xcat_host(self, node, ip, host_name):
        """Add/Update hostname/ip bundle in xCAT MN nodes table."""
        commands = "node=%s" % node + " hosts.ip=%s" % ip
        commands += " hosts.hostnames=%s" % host_name
        body = [commands]
        url = self._xcat_url.tabch("/hosts")

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            result_data = zvmutils.xcat_request("PUT", url, body)['data']

        return result_data

    def _delete_xcat_host(self, node_name):
        """Remove xcat hosts table rows where node name is node_name."""
        commands = "-d node=%s hosts" % node_name
        body = [commands]
        url = self._xcat_url.tabch("/hosts")

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url, body)['data']

    def add_xcat_mac(self, node, interface, mac, zhcp=None):
        """Add node name, interface, mac address into xcat mac table."""
        commands = "mac.node=%s" % node + " mac.mac=%s" % mac
        commands += " mac.interface=%s" % interface
        if zhcp is not None:
            commands += " mac.comments=%s" % zhcp
        url = self._xcat_url.tabch("/mac")
        body = [commands]

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url, body)['data']

    def add_xcat_switch(self, node, nic_name, interface, zhcp=None):
        """Add node name and nic name address into xcat switch table."""
        commands = "switch.node=%s" % node
        commands += " switch.port=%s" % nic_name
        commands += " switch.interface=%s" % interface
        if zhcp is not None:
            commands += " switch.comments=%s" % zhcp
        url = self._xcat_url.tabch("/switch")
        body = [commands]

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url, body)['data']

    def _delete_xcat_mac(self, node_name):
        """Remove node mac record from xcat mac table."""
        commands = "-d node=%s mac" % node_name
        url = self._xcat_url.tabch("/mac")
        body = [commands]

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url, body)['data']

    def _delete_xcat_switch(self, node_name):
        """Remove node switch record from xcat switch table."""
        commands = "-d node=%s switch" % node_name
        url = self._xcat_url.tabch("/switch")
        body = [commands]

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url, body)['data']

    def update_xcat_mac(self, node, interface, mac, zhcp=None):
        """Add node name, interface, mac address into xcat mac table."""
        commands = "node=%s" % node + " interface=%s" % interface
        commands += " mac.mac=%s" % mac
        if zhcp is not None:
            commands += " mac.comments=%s" % zhcp
        url = self._xcat_url.tabch("/mac")
        body = [commands]

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url, body)['data']

    def update_xcat_switch(self, node, nic_name, interface, zhcp=None):
        """Add node name and nic name address into xcat switch table."""
        commands = "node=%s" % node
        commands += " interface=%s" % interface
        commands += " switch.port=%s" % nic_name
        if zhcp is not None:
            commands += " switch.comments=%s" % zhcp
        url = self._xcat_url.tabch("/switch")
        body = [commands]

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url, body)['data']

    def clean_mac_switch_host(self, node_name):
        """Clean node records in xCAT mac, host and switch table."""
        self.clean_mac_switch(node_name)
        self._delete_xcat_host(node_name)

    def clean_mac_switch(self, node_name):
        """Clean node records in xCAT mac and switch table."""
        self._delete_xcat_mac(node_name)
        self._delete_xcat_switch(node_name)

    def makehosts(self):
        """Update xCAT MN /etc/hosts file."""
        url = self._xcat_url.network("/makehosts")
        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url)['data']

    def makeDNS(self):
        """Update xCAT MN DNS."""
        url = self._xcat_url.network("/makedns")
        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            return zvmutils.xcat_request("PUT", url)['data']

    def config_xcat_mac(self, instance_name):
        """Hook xCat to prevent assign MAC for instance."""
        fake_mac_addr = "00:00:00:00:00:00"
        nic_name = "fake"
        self.add_xcat_mac(instance_name, nic_name, fake_mac_addr)

    def create_nic(self, zhcpnode, inst_name, nic_name, mac_address, vdev,
                   userid=None):
        """Create network information in xCAT and zVM user direct."""
        macid = mac_address.replace(':', '')[-6:]
        # Ensure the updated driver code can works with old version of xCAT
        if zvmutils.xcat_support_chvm_smcli():
            self._add_instance_nic_by_chvm(inst_name, vdev, macid)
        else:
            self._add_instance_nic(zhcpnode, inst_name, vdev, macid, userid)
        self._delete_xcat_mac(inst_name)
        self.add_xcat_mac(inst_name, vdev, mac_address, zhcpnode)
        self.add_xcat_switch(inst_name, nic_name, vdev, zhcpnode)

    def _add_instance_nic(self, zhcpnode, inst_name, vdev, macid, userid=None):
        """Add NIC definition into user direct."""
        if userid is None:
            command = ("/opt/zhcp/bin/smcli Image_Definition_Update_DM -T %s" %
                       inst_name)
        else:
            command = ("/opt/zhcp/bin/smcli Image_Definition_Update_DM -T %s" %
                       userid)
        command += " -k \'NICDEF=VDEV=%s TYPE=QDIO " % vdev
        command += "MACID=%s\'" % macid
        try:
            zvmutils.xdsh(zhcpnode, command)
        except exception.ZVMXCATXdshFailed as err:
            msg = _("Adding nic error: %s") % err.format_message()
            raise exception.ZVMNetworkError(msg=msg)

    def _add_instance_nic_by_chvm(self, nodename, vdev, macid):
        """Add NIC defination into user direct"""
        command = 'Image_Definition_Update_DM -T  %userid%'
        command += ' -k \'NICDEF=VDEV=%s TYPE=QDIO ' % vdev
        command += 'MACID=%s\'' % macid
        body = ['--smcli', command]
        url = self._xcat_url.chvm('/' + nodename)
        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMNetworkError):
            zvmutils.xcat_request("PUT", url, body)
