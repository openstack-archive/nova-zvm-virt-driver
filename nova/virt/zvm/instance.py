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


import binascii
import datetime
import time

from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import loopingcall
from oslo_utils import timeutils

from nova.compute import power_state
from nova import exception as nova_exception
from nova.i18n import _, _LW
from nova.virt import hardware
from nova.virt.zvm import const
from nova.virt.zvm import dist
from nova.virt.zvm import exception
from nova.virt.zvm import utils as zvmutils
from nova.virt.zvm import volumeop

LOG = logging.getLogger(__name__)
CONF = cfg.CONF


CONF.import_opt('default_ephemeral_format', 'nova.conf')


class ZVMInstance(object):
    '''OpenStack instance that running on of z/VM hypervisor.'''

    def __init__(self, instance=None):
        """Initialize instance attributes for database."""
        instance = instance or {}
        self._xcat_url = zvmutils.get_xcat_url()
        self._instance = instance
        self._name = instance['name']
        self._volumeop = volumeop.VolumeOperator()
        self._dist_manager = dist.ListDistManager()

    def power_off(self, timeout=0, retry_interval=10):
        """Power off z/VM instance."""
        try:
            self._power_state("PUT", "softoff")
        except exception.ZVMXCATInternalError as err:
            err_str = err.format_message()
            if ("Return Code: 200" in err_str and
                    "Reason Code: 12" in err_str):
                # Instance already not active
                LOG.warning(_LW("z/VM instance %s not active") % self._name)
                return
            else:
                msg = _("Failed to power off instance: %s") % err_str
                LOG.error(msg)
                raise nova_exception.InstancePowerOffFailure(reason=msg)

        timeout = timeout or CONF.shutdown_timeout
        retry_interval = retry_interval or 10
        retry_count = timeout // retry_interval
        while (retry_count > 0):
            if self._get_power_stat() == power_state.SHUTDOWN:
                # In shutdown state already
                return
            else:
                time.sleep(retry_interval)
                retry_count -= 1

        LOG.warning(_LW("Failed to shutdown instance %(inst)s in %(time)d "
                     "seconds") % {'inst': self._name, 'time': timeout})

    def power_on(self):
        """"Power on z/VM instance."""
        try:
            self._power_state("PUT", "on")
        except exception.ZVMXCATInternalError as err:
            err_str = err.format_message()
            if ("Return Code: 200" in err_str and
                    "Reason Code: 8" in err_str):
                # Instance already not active
                LOG.warning(_LW("z/VM instance %s already active")
                            % self._name)
                return

        self._wait_for_reachable()
        if not self._reachable:
            LOG.error(_("Failed to power on instance %s: timeout") %
                      self._name)
            raise nova_exception.InstancePowerOnFailure(reason="timeout")

    def reset(self):
        """Hard reboot z/VM instance."""
        try:
            self._power_state("PUT", "reset")
        except exception.ZVMXCATInternalError as err:
            err_str = err.format_message()
            if ("Return Code: 200" in err_str and
                    "Reason Code: 12" in err_str):
                # Be able to reset in power state of SHUTDOWN
                LOG.warning(_LW("Reset z/VM instance %s from SHUTDOWN state") %
                         self._name)
                return
            else:
                raise err
        self._wait_for_reachable()

    def reboot(self):
        """Soft reboot z/VM instance."""
        self._power_state("PUT", "reboot")
        self._wait_for_reachable()

    def pause(self):
        """Pause the z/VM instance."""
        self._power_state("PUT", "pause")

    def unpause(self):
        """Unpause the z/VM instance."""
        self._power_state("PUT", "unpause")
        self._wait_for_reachable()

    def attach_volume(self, volumeop, context, connection_info, instance,
                      mountpoint, is_active, rollback=True):
        volumeop.attach_volume_to_instance(context, connection_info,
                                           instance, mountpoint,
                                           is_active, rollback)

    def detach_volume(self, volumeop, connection_info, instance, mountpoint,
                      is_active, rollback=True):
        volumeop.detach_volume_from_instance(connection_info,
                                             instance, mountpoint,
                                             is_active, rollback)

    def get_info(self):
        """Get the current status of an z/VM instance."""
        _instance_info = hardware.InstanceInfo()

        power_stat = self._get_power_stat()
        is_reachable = self.is_reachable()

        max_mem_kb = int(self._instance['memory_mb']) * 1024
        if is_reachable:
            try:
                rec_list = self._get_rinv_info()
            except exception.ZVMXCATInternalError:
                raise nova_exception.InstanceNotFound(instance_id=self._name)

            try:
                mem = self._get_current_memory(rec_list)
                num_cpu = self._get_cpu_count(rec_list)
                cpu_time = self._get_cpu_used_time(rec_list)
                _instance_info.state = power_stat
                _instance_info.max_mem_kb = max_mem_kb
                _instance_info.mem_kb = mem
                _instance_info.num_cpu = num_cpu
                _instance_info.cpu_time_ns = cpu_time

            except exception.ZVMInvalidXCATResponseDataError:
                LOG.warning(_LW("Failed to get inventory info for %s")
                    % self._name)
                _instance_info.state = power_stat
                _instance_info.max_mem_kb = max_mem_kb
                _instance_info.mem_kb = max_mem_kb
                _instance_info.num_cpu = self._instance['vcpus']
                _instance_info.cpu_time_ns = 0

        else:
            # Since xCAT rinv can't get info from a server that in power state
            # of SHUTDOWN or PAUSED
            if ((power_stat == power_state.RUNNING) and
                    (self._instance['power_state'] == power_state.PAUSED)):
                # return paused state only previous power state is paused
                _instance_info.state = power_state.PAUSED
                _instance_info.max_mem_kb = max_mem_kb
                _instance_info.mem_kb = max_mem_kb
                _instance_info.num_cpu = self._instance['vcpus']
                _instance_info.cpu_time_ns = 0
            else:
                # otherwise return xcat returned state
                _instance_info.state = power_stat
                _instance_info.max_mem_kb = max_mem_kb
                _instance_info.mem_kb = 0
                _instance_info.num_cpu = self._instance['vcpus']
                _instance_info.cpu_time_ns = 0
        return _instance_info

    def create_xcat_node(self, zhcp, userid=None):
        """Create xCAT node for z/VM instance."""
        LOG.debug("Creating xCAT node for %s" % self._name)

        user_id = userid or self._name
        body = ['userid=%s' % user_id,
                'hcp=%s' % zhcp,
                'mgt=zvm',
                'groups=%s' % CONF.zvm_xcat_group]
        url = self._xcat_url.mkdef('/' + self._name)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMXCATCreateNodeFailed, node=self._name):
            zvmutils.xcat_request("POST", url, body)

    def create_userid(self, block_device_info, image_meta, context,
                      os_image=None):
        """Create z/VM userid into user directory for a z/VM instance."""
        # We do not support boot from volume currently
        LOG.debug("Creating the z/VM user entry for instance %s"
                      % self._name)

        boot_from_volume = zvmutils.is_boot_from_volume(block_device_info)[1]

        eph_disks = block_device_info.get('ephemerals', [])
        kwprofile = 'profile=%s' % CONF.zvm_user_profile
        body = [kwprofile,
                'password=%s' % CONF.zvm_user_default_password,
                'cpu=%i' % self._instance['vcpus'],
                'memory=%im' % self._instance['memory_mb'],
                'privilege=%s' % CONF.zvm_user_default_privilege]

        # image_meta passed from spawn is a dict, in resize is a object
        if isinstance(image_meta, dict):
            if 'name' in image_meta.keys():
                kwimage = 'imagename=%s' % image_meta['name']
                body.append(kwimage)
        else:
            image_name = getattr(image_meta, 'name')
            if image_name:
                kwimage = 'imagename=%s' % image_name
                body.append(kwimage)

        if os_image:
            kwimage = 'osimage=%s' % os_image
            body.append(kwimage)

        # Versions of xCAT that do not understand the instance ID and
        # request ID will silently ignore them.
        url = self._xcat_url.mkvm('/' + self._name, self._instance.uuid,
                                  context)

        try:
            zvmutils.xcat_request("POST", url, body)

            if not boot_from_volume:
                size = '%ig' % self._instance['root_gb']
                # use a flavor the disk size is 0
                if size == '0g':
                    size = image_meta['properties']['root_disk_units']
                # Add root disk and set ipl
                self.add_mdisk(CONF.zvm_diskpool,
                               CONF.zvm_user_root_vdev,
                               size)
                self._set_ipl(CONF.zvm_user_root_vdev)

            # Add additional ephemeral disk
            if self._instance['ephemeral_gb'] != 0:
                if eph_disks == []:
                    # Create ephemeral disk according to flavor
                    fmt = (CONF.default_ephemeral_format or
                           const.DEFAULT_EPH_DISK_FMT)
                    self.add_mdisk(CONF.zvm_diskpool,
                                   CONF.zvm_user_adde_vdev,
                                   '%ig' % self._instance['ephemeral_gb'],
                                   fmt)
                else:
                    # Create ephemeral disks according --ephemeral option
                    for idx, eph in enumerate(eph_disks):
                        vdev = (eph.get('vdev') or
                                zvmutils.generate_eph_vdev(idx))
                        size = eph['size']
                        size_in_units = eph.get('size_in_units', False)
                        if not size_in_units:
                            size = '%ig' % size
                        fmt = (eph.get('guest_format') or
                               CONF.default_ephemeral_format or
                               const.DEFAULT_EPH_DISK_FMT)
                        self.add_mdisk(CONF.zvm_diskpool, vdev, size, fmt)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMDriverError) as err:
            msg = _("Failed to create z/VM userid: %s") % err.format_message()
            LOG.error(msg)
            raise exception.ZVMXCATCreateUserIdFailed(instance=self._name,
                                                      msg=msg)

    def prepare_volume_boot(self, context, instance, block_device_mapping,
                            root_device, volume_meta):
        try:
            connection_info = self._volumeop.get_root_volume_connection_info(
                                    block_device_mapping, root_device)
            (lun, wwpn, size,
             fcp) = self._volumeop.extract_connection_info(context,
                                                            connection_info)

            (kernel_parm_string, scpdata) = self._forge_hex_scpdata(fcp,
                                                        wwpn, lun, volume_meta)

            loaddev_str = "%(wwpn)s %(lun)s 1 %(scpdata)s" % {'wwpn': wwpn,
                                'lun': lun, 'scpdata': scpdata}

            self._volumeop.volume_boot_init(instance, fcp)
            self._set_ipl(fcp)
            self._set_loaddev(loaddev_str)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError,
                exception.ZVMDriverError) as err:
            msg = _("Failed to prepare volume to boot") % err.format_message()
            LOG.error(msg)
            raise exception.ZVMVolumeError(msg=msg)

        return (lun, wwpn, size, fcp)

    def clean_volume_boot(self, context, instance, block_device_mapping,
                            root_device):
        try:
            connection_info = self._volumeop.get_root_volume_connection_info(
                                    block_device_mapping, root_device)
            (lun, wwpn, size,
             fcp) = self._volumeop.extract_connection_info(context,
                                                            connection_info)
            self._volumeop.volume_boot_cleanup(instance, fcp)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError,
                exception.ZVMDriverError) as err:
            emsg = err.format_message()
            msg = _LW("Failed to clean boot from volume "
                      "preparations: %s") % emsg
            LOG.warning(msg)
            raise exception.ZVMVolumeError(msg=msg)

    def _forge_hex_scpdata(self, fcp, wwpn, lun, volume_meta):
        """Forge scpdata in string form and HEX form."""
        root = volume_meta['root']
        os_version = volume_meta['os_version']
        linux_dist = self._dist_manager.get_linux_dist(os_version)()
        scp_string = linux_dist.get_scp_string(root, fcp, wwpn, lun)

        # Convert to HEX string
        # Without Encode / Decode it will still work for python 2.6 but not for
        # Python 3
        try:
            scp_string_ascii = scp_string.encode('ascii')
            scp_string_hex = binascii.hexlify(scp_string_ascii)
            scp_data = scp_string_hex.decode('ascii')
        except Exception as err:
            errmsg = _("Failed to forge hex scpdata: %s") % err
            LOG.error(errmsg)
            raise exception.ZVMDriverError(msg=errmsg)

        return (scp_string, scp_data)

    def _set_ipl(self, ipl_state):
        body = ["--setipl %s" % ipl_state]
        url = self._xcat_url.chvm('/' + self._name)
        zvmutils.xcat_request("PUT", url, body)

    def _set_loaddev(self, loaddev):
        body = ["--setloaddev %s" % loaddev]
        url = self._xcat_url.chvm('/' + self._name)
        zvmutils.xcat_request("PUT", url, body)

    def is_locked(self, zhcp_node):
        cmd = "/opt/zhcp/bin/smcli Image_Lock_Query_DM -T %s" % self._name
        resp = zvmutils.xdsh(zhcp_node, cmd)

        return "is Unlocked..." not in str(resp)

    def _wait_for_unlock(self, zhcp_node, interval=60, timeout=300):
        LOG.debug("Waiting for unlock instance %s" % self._name)

        def _wait_unlock(expiration):
            if timeutils.utcnow() > expiration:
                LOG.debug("Waiting for unlock instance %s timeout" %
                          self._name)
                raise loopingcall.LoopingCallDone()

            if not self.is_locked(zhcp_node):
                LOG.debug("Instance %s is unlocked" %
                         self._name)
                raise loopingcall.LoopingCallDone()

        expiration = timeutils.utcnow() + datetime.timedelta(seconds=timeout)

        timer = loopingcall.FixedIntervalLoopingCall(_wait_unlock,
                                                     expiration)
        timer.start(interval=interval).wait()

    def _delete_userid(self, url):
        try:
            zvmutils.xcat_request("DELETE", url)
        except exception.ZVMXCATInternalError as err:
            emsg = err.format_message()
            LOG.debug("error emsg in delete_userid: %s", emsg)
            if (emsg.__contains__("Return Code: 400") and
                    emsg.__contains__("Reason Code: 4")):
                # zVM user definition not found, delete xCAT node directly
                self.delete_xcat_node()
            else:
                raise

    def delete_userid(self, zhcp_node, context):
        """Delete z/VM userid for the instance.This will remove xCAT node
        at same time.
        """
        # Versions of xCAT that do not understand the instance ID and
        # request ID will silently ignore them.
        url = self._xcat_url.rmvm('/' + self._name, self._instance.uuid,
                                  context)

        try:
            self._delete_userid(url)
        except exception.ZVMXCATInternalError as err:
            emsg = err.format_message()
            if (emsg.__contains__("Return Code: 400") and
               (emsg.__contains__("Reason Code: 16") or
                emsg.__contains__("Reason Code: 12"))):
                # The vm or vm device was locked. Unlock before deleting
                self._wait_for_unlock(zhcp_node)
                self._delete_userid(url)
            else:
                LOG.debug("exception not able to handle in delete_userid "
                          "%s", self._name)
                raise err
        except exception.ZVMXCATRequestFailed as err:
            emsg = err.format_message()
            if (emsg.__contains__("Invalid nodes and/or groups") and
                    emsg.__contains__("Forbidden")):
                # Assume neither zVM userid nor xCAT node exist in this case
                return
            else:
                raise err

    def delete_xcat_node(self):
        """Remove xCAT node for z/VM instance."""
        url = self._xcat_url.rmdef('/' + self._name)
        try:
            zvmutils.xcat_request("DELETE", url)
        except exception.ZVMXCATInternalError as err:
            if err.format_message().__contains__("Could not find an object"):
                # The xCAT node not exist
                return
            else:
                raise err

    def add_mdisk(self, diskpool, vdev, size, fmt=None):
        """Add a 3390 mdisk for a z/VM user.

        NOTE: No read, write and multi password specified, and
        access mode default as 'MR'.

        """
        disk_type = CONF.zvm_diskpool_type
        if (disk_type == 'ECKD'):
            action = '--add3390'
        elif (disk_type == 'FBA'):
            action = '--add9336'
        else:
            errmsg = _("Disk type %s is not supported.") % disk_type
            LOG.error(errmsg)
            raise exception.ZVMDriverError(msg=errmsg)

        if fmt:
            body = [" ".join([action, diskpool, vdev, size, "MR", "''", "''",
                    "''", fmt])]
        else:
            body = [" ".join([action, diskpool, vdev, size])]
        url = self._xcat_url.chvm('/' + self._name)
        zvmutils.xcat_request("PUT", url, body)

    def _power_state(self, method, state):
        """Invoke xCAT REST API to set/get power state for a instance."""
        body = [state]
        url = self._xcat_url.rpower('/' + self._name)
        return zvmutils.xcat_request(method, url, body)

    def _get_power_stat(self):
        """Get power status of a z/VM instance."""
        LOG.debug('Query power stat of %s' % self._name)
        res_dict = self._power_state("GET", "stat")

        @zvmutils.wrap_invalid_xcat_resp_data_error
        def _get_power_string(d):
            tempstr = d['info'][0][0]
            return tempstr[(tempstr.find(':') + 2):].strip()

        power_stat = _get_power_string(res_dict)
        return zvmutils.mapping_power_stat(power_stat)

    def _get_rinv_info(self):
        """get rinv result and return in a list."""
        url = self._xcat_url.rinv('/' + self._name, '&field=cpumem')
        LOG.debug('Remote inventory of %s' % self._name)
        res_info = zvmutils.xcat_request("GET", url)['info']

        with zvmutils.expect_invalid_xcat_resp_data(res_info):
            rinv_info = res_info[0][0].split('\n')

        return rinv_info

    @zvmutils.wrap_invalid_xcat_resp_data_error
    def _modify_storage_format(self, mem):
        """modify storage from 'G' ' M' to 'K'."""
        # Only special case for 0
        if mem == '0':
            return 0

        new_mem = 0
        if mem.endswith('G'):
            new_mem = int(mem[:-1]) * 1024 * 1024
        elif mem.endswith('M'):
            new_mem = int(mem[:-1]) * 1024
        elif mem.endswith('K'):
            new_mem = int(mem[:-1])
        else:
            exp = "ending with a 'G', 'M' or 'K'"
            errmsg = _("Invalid memory format: %(invalid)s; Expected: "
                       "%(exp)s") % {'invalid': mem, 'exp': exp}
            LOG.error(errmsg)
            raise exception.ZVMInvalidXCATResponseDataError(msg=errmsg)
        return new_mem

    @zvmutils.wrap_invalid_xcat_resp_data_error
    def _get_current_memory(self, rec_list):
        """Return the max memory can be used."""
        _mem = None

        for rec in rec_list:
            if rec.__contains__("Total Memory: "):
                tmp_list = rec.split()
                _mem = tmp_list[3]

        _mem = self._modify_storage_format(_mem)
        return _mem

    @zvmutils.wrap_invalid_xcat_resp_data_error
    def _get_cpu_count(self, rec_list):
        """Return the virtual cpu count."""
        _cpu_flag = False
        num_cpu = 0

        for rec in rec_list:
            if (_cpu_flag is True):
                tmp_list = rec.split()
                if (len(tmp_list) > 1):
                    if (tmp_list[1] == "CPU"):
                        num_cpu += 1
                    else:
                        _cpu_flag = False
            if rec.__contains__("Processors: "):
                _cpu_flag = True

        return num_cpu

    @zvmutils.wrap_invalid_xcat_resp_data_error
    def _get_cpu_used_time(self, rec_list):
        """Return the cpu used time in."""
        cpu_time = None

        for rec in rec_list:
            if rec.__contains__("CPU Used Time: "):
                tmp_list = rec.split()
                cpu_time = tmp_list[4]

        return int(cpu_time)

    def is_reachable(self):
        """Return True is the instance is reachable."""
        url = self._xcat_url.nodestat('/' + self._name)
        LOG.debug('Get instance status of %s' % self._name)
        res_dict = zvmutils.xcat_request("GET", url)

        with zvmutils.expect_invalid_xcat_resp_data(res_dict):
            status = res_dict['node'][0][0]['data'][0]

        if status is not None:
            if status.__contains__('sshd'):
                return True

        return False

    def _wait_for_reachable(self):
        """Called at an interval until the instance is reachable."""
        self._reachable = False

        def _check_reachable():
            if not self.is_reachable():
                raise exception.ZVMRetryException()
            else:
                self._reachable = True

        zvmutils.looping_call(_check_reachable, 5, 5, 30,
                              CONF.zvm_reachable_timeout,
                              exception.ZVMRetryException)

    def update_node_info(self, image_meta):
        LOG.debug("Update the node info for instance %s" % self._name)

        image_name = ''.join(i for i in image_meta['name'] if i.isalnum())
        image_id = image_meta['id']
        os_type = image_meta['properties']['os_version']
        os_arch = image_meta['properties']['architecture']
        prov_method = image_meta['properties']['provisioning_method']
        profile_name = '_'.join((image_name, image_id.replace('-', '_')))

        body = ['noderes.netboot=%s' % const.HYPERVISOR_TYPE,
                'nodetype.os=%s' % os_type,
                'nodetype.arch=%s' % os_arch,
                'nodetype.provmethod=%s' % prov_method,
                'nodetype.profile=%s' % profile_name]
        url = self._xcat_url.chtab('/' + self._name)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMXCATUpdateNodeFailed, node=self._name):
            zvmutils.xcat_request("PUT", url, body)

    def update_node_info_resize(self, image_name_xcat):
        LOG.debug("Update the nodetype for instance %s" % self._name)

        name_section = image_name_xcat.split("-")
        os_type = name_section[0]
        os_arch = name_section[1]
        profile_name = name_section[3]

        body = ['noderes.netboot=%s' % const.HYPERVISOR_TYPE,
                'nodetype.os=%s' % os_type,
                'nodetype.arch=%s' % os_arch,
                'nodetype.provmethod=%s' % 'sysclone',
                'nodetype.profile=%s' % profile_name]

        url = self._xcat_url.chtab('/' + self._name)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMXCATUpdateNodeFailed, node=self._name):
            zvmutils.xcat_request("PUT", url, body)

    def get_provmethod(self):
        addp = "&col=node=%s&attribute=provmethod" % self._name
        url = self._xcat_url.gettab('/nodetype', addp)
        res_info = zvmutils.xcat_request("GET", url)
        return res_info['data'][0][0]

    def update_node_provmethod(self, provmethod):
        LOG.debug("Update the nodetype for instance %s" % self._name)

        body = ['nodetype.provmethod=%s' % provmethod]

        url = self._xcat_url.chtab('/' + self._name)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMXCATUpdateNodeFailed, node=self._name):
            zvmutils.xcat_request("PUT", url, body)

    def update_node_def(self, hcp, userid):
        """Update xCAT node definition."""

        body = ['zvm.hcp=%s' % hcp,
                'zvm.userid=%s' % userid]
        url = self._xcat_url.chtab('/' + self._name)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMXCATUpdateNodeFailed, node=self._name):
            zvmutils.xcat_request("PUT", url, body)

    def deploy_node(self, image_name, transportfiles=None, vdev=None):
        LOG.debug("Begin to deploy image on instance %s" % self._name)
        vdev = vdev or CONF.zvm_user_root_vdev
        remote_host_info = zvmutils.get_host()
        body = ['netboot',
                'device=%s' % vdev,
                'osimage=%s' % image_name]

        if transportfiles:
            body.append('transport=%s' % transportfiles)
            body.append('remotehost=%s' % remote_host_info)

        url = self._xcat_url.nodeset('/' + self._name)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMXCATDeployNodeFailed, node=self._name):
            zvmutils.xcat_request("PUT", url, body)

    def copy_xcat_node(self, source_node_name):
        """Create xCAT node from an existing z/VM instance."""
        LOG.debug("Creating xCAT node %s from existing node" % self._name)

        url = self._xcat_url.lsdef_node('/' + source_node_name)
        res_info = zvmutils.xcat_request("GET", url)['info'][0]

        body = []
        for info in res_info:
            if ("=" in info and ("postbootscripts" not in info) and
                    ("postscripts" not in info) and ("hostnames" not in info)):
                body.append(info.lstrip())

        url = self._xcat_url.mkdef('/' + self._name)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMXCATCreateNodeFailed, node=self._name):
            zvmutils.xcat_request("POST", url, body)

    def get_console_log(self, logsize):
        """get console log."""
        url = self._xcat_url.rinv('/' + self._name, '&field=--consoleoutput'
                                  '&field=%s') % logsize

        res_info = zvmutils.xcat_request("GET", url)

        with zvmutils.expect_invalid_xcat_resp_data(res_info):
            log_data = res_info['info'][0][0]

        return log_data
