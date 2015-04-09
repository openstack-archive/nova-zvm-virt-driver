# Copyright 2015 IBM Corp.
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

import abc

from oslo_log import log as logging
import six


LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class LinuxDist(object):
    """Linux distribution base class

    Due to we need to interact with linux dist and inject different files
    according to the dist version. Currently only RHEL and SLES are supported
    """

    def create_network_configuration_files(self, file_path, network_info,
                                           base_vdev, os_type):
        """Generate network configuration files to instance."""
        device_num = 0
        cfg_files = []
        cmd_strings = ''
        udev_cfg_str = ''
        dns_cfg_str = ''
        route_cfg_str = ''
        cmd_str = None
        file_path = self._get_network_file_path()
        file_name_route = file_path + 'routes'

        file_name_dns = self._get_dns_filename()
        for vif in network_info:
            file_name = self._get_device_name(device_num)
            network = vif['network']
            (cfg_str, cmd_str, dns_str,
                route_str) = self._generate_network_configration(network,
                                                base_vdev, device_num, os_type)
            LOG.debug('Network configure file content is: %s' % cfg_str)
            target_net_conf_file_name = file_path + file_name
            cfg_files.append((target_net_conf_file_name, cfg_str))
            udev_cfg_str += self.generate_udev_configuration(device_num,
                                '0.0.' + str(base_vdev).zfill(4))
            if cmd_str is not None:
                cmd_strings += cmd_str
            if len(dns_str) > 0:
                dns_cfg_str += dns_str
            if len(route_str) > 0:
                route_cfg_str += route_str
            base_vdev = str(hex(int(base_vdev, 16) + 3))[2:]
            device_num += 1

        if len(dns_cfg_str) > 0:
            cfg_files.append((file_name_dns, dns_cfg_str))
        self._append_udev_info(cfg_files, file_name_route, route_cfg_str,
                               udev_cfg_str)

    def _generate_network_configuration(self, network, vdev, device_num,
                                        os_type):
        ip_v4 = dns_str = ''

        subnets_v4 = [s for s in network['subnets'] if s['version'] == 4]

        if len(subnets_v4[0]['ips']) > 0:
            ip_v4 = subnets_v4[0]['ips'][0]['address']
        if len(subnets_v4[0]['dns']) > 0:
            for dns in subnets_v4[0]['dns']:
                dns_str += 'nameserver ' + dns['address'] + '\n'
        netmask_v4 = str(subnets_v4[0].as_netaddr().netmask)
        gateway_v4 = subnets_v4[0]['gateway']['address'] or ''
        broadcast_v4 = str(subnets_v4[0].as_netaddr().broadcast)
        device = "eth" + str(device_num)
        address_read = str(vdev).zfill(4)
        address_write = str(hex(int(vdev, 16) + 1))[2:].zfill(4)
        address_data = str(hex(int(vdev, 16) + 2))[2:].zfill(4)
        subchannels = '0.0.%s' % address_read.lower()
        subchannels += ',0.0.%s' % address_write.lower()
        subchannels += ',0.0.%s' % address_data.lower()

        cfg_str = self.get_cfg_str(device, broadcast_v4, gateway_v4,
                                   ip_v4, netmask_v4, address_read,
                                   subchannels)
        cmd_str = self._get_cmd_str(address_read, address_write,
                                    address_data)
        route_str = self._get_route_str(gateway_v4)

        return cfg_str, cmd_str, dns_str, route_str

    @abc.abstractmethod
    def _get_network_file_path(self):
        """Get network file configuration path."""
        pass

    @abc.abstractmethod
    def get_change_passwd_command(self, admin_password):
        """construct change password command

        :admin_password: the password to be changed to
        """
        pass

    @abc.abstractmethod
    def _get_cfg_str(self, device, broadcast_v4, gateway_v4, ip_v4,
                     netmask_v4, address_read, subchannels):
        """construct configuration file of network device."""
        pass

    @abc.abstractmethod
    def _get_device_name(self, device_num):
        """construct the name of a network device file."""
        pass

    @abc.abstractmethod
    def _get_route_str(self, gateway_v4):
        """construct a router string."""
        pass

    @abc.abstractmethod
    def _get_cmd_str(self, address_read, address_write, address_data):
        """construct network startup command string."""
        pass

    @abc.abstractmethod
    def _get_dns_filename(self):
        """construct the name of dns file."""
        pass

    @abc.abstractmethod
    def _get_znetconfig_contents(self):
        """construct znetconfig file will be called during first boot."""
        pass


class Rhel(LinuxDist):
    def _get_network_file_path():
        return '/etc/sysconfig/network-scripts/'

    def get_change_passwd_command(self, admin_password):
        return 'echo %s|passwd --stdin root' % admin_password

    def _get_cfg_str(self, device, broadcast_v4, gateway_v4, ip_v4,
                     netmask_v4, address_read, subchannels):
        cfg_str = 'DEVICE=\"' + device + '\"\n'
        cfg_str += 'BOOTPROTO=\"static\"\n'
        cfg_str += 'BROADCAST=\"' + broadcast_v4 + '\"\n'
        cfg_str += 'GATEWAY=\"' + gateway_v4 + '\"\n'
        cfg_str += 'IPADDR=\"' + ip_v4 + '\"\n'
        cfg_str += 'NETMASK=\"' + netmask_v4 + '\"\n'
        cfg_str += 'NETTYPE=\"qeth\"\n'
        cfg_str += 'ONBOOT=\"yes\"\n'
        cfg_str += 'PORTNAME=\"PORT' + address_read + '\"\n'
        cfg_str += 'OPTIONS=\"layer2=1\"\n'
        cfg_str += 'SUBCHANNELS=\"' + subchannels + '\"\n'
        return cfg_str

    def _get_device_name(self, device_num):
        return 'ifcfg-eth' + str(device_num)

    def _get_route_str(self, gateway_v4):
        return ''

    def _get_cmd_str(self, address_read, address_write, address_data):
        return ''

    def _get_dns_filename(self):
        return '/etc/resolv.conf'


class Rhel6(Rhel):
    def _get_znetconfig_contents(self):
        return '\n'.join(('cio_ignore -R',
                          'znetconf -R <<EOF',
                          'y',
                          'EOF',
                          'udevadm trigger',
                          'udevadm settle',
                          'sleep 2',
                          'znetconf -A',
                          'service network restart',
                          'cio_ignore -u'))


class Rhel7(Rhel):
    def _get_znetconfig_contents(self):
        return '\n'.join(('cio_ignore -R',
                          'znetconf -R <<EOF',
                          'y',
                          'EOF',
                          'udevadm trigger',
                          'udevadm settle',
                          'sleep 2',
                          'znetconf -A',
                          'cio_ignore -u'))


class Sles(LinuxDist):
    def _get_network_file_path():
        return '/etc/sysconfig/network-scripts/'

    def get_change_passwd_command(self, admin_password):
        return 'echo %s|passwd --stdin root' % admin_password

    def _get_cidr_from_ip_netmask(self, ip, netmask):
        netmask_fields = netmask.split('.')
        bin_str = ''
        for octet in netmask_fields:
            bin_str += bin(int(octet))[2:].zfill(8)
        mask = str(len(bin_str.rstrip('0')))
        cidr_v4 = ip + '/' + mask
        return cidr_v4

    def _get_cfg_str(self, device, broadcast_v4, gateway_v4, ip_v4,
                     netmask_v4, address_read, subchannels):
        cidr_v4 = self._get_cidr_from_ip_netmask(ip_v4, netmask_v4)
        cfg_str = "BOOTPROTO=\'static\'\n"
        cfg_str += "IPADDR=\'%s\'\n" % cidr_v4
        cfg_str += "BROADCAST=\'%s\'\n" % broadcast_v4
        cfg_str += "STARTMODE=\'onboot\'\n"
        cfg_str += ("NAME=\'OSA Express Network card (%s)\'\n" %
                    address_read)
        return cfg_str

    def _get_route_str(self, gateway_v4):
        route_str = 'default %s - -\n' % gateway_v4
        return route_str

    def _get_cmd_str(self, address_read, address_write, address_data):
        cmd_str = 'qeth_configure -l 0.0.%s ' % address_read.lower()
        cmd_str += '0.0.%(write)s 0.0.%(data)s 1\n' % {'write':
                address_write.lower(), 'data': address_data.lower()}
        return cmd_str

    def _get_dns_filename(self):
        return '/etc/resolv.conf'


class Sles11(Sles):
    def _get_znetconfig_contenst(self):
        return '\n'.join(('cio_ignore -R',
                          'udevadm trigger',
                          'udevadm settle',
                          'sleep 2',
                          'service network restart',
                          'cio_ignore -u'))


class Sles12(Sles):
    def _get_znetconfig_contenst(self):
        return '\n'.join(('cio_ignore -R',
                          'udevadm trigger',
                          'udevadm settle',
                          'sleep 2',
                          'cio_ignore -u'))
