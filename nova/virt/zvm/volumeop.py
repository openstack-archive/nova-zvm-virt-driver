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

import contextlib
import re
import time

from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils

import nova.context
from nova.i18n import _
from nova.objects import block_device as block_device_obj
from nova.objects import instance as instance_obj
from nova.virt.zvm import const
from nova.virt.zvm import exception
from nova.virt.zvm import utils as zvmutils
from nova import volume


LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class VolumeOperator(object):
    """Volume operator on IBM z/VM platform."""

    def __init__(self):
        self._svc_driver = SVCDriver()

    def init_host(self, host_stats):
        try:
            self._svc_driver.init_host(host_stats)
        except (exception.ZVMDriverError, exception.ZVMVolumeError) as err:
            LOG.warning(_("Initialize zhcp failed. Reason: %s") %
                        err.format_message())

    def attach_volume_to_instance(self, context, connection_info, instance,
                                  mountpoint, is_active, rollback=True):
        """Attach a volume to an instance."""

        if None in [connection_info, instance, is_active]:
            errmsg = _("Missing required parameters.")
            raise exception.ZVMDriverError(msg=errmsg)

        LOG.debug("Attach a volume to an instance. conn_info: %(info)s; " +
                    "instance: %(name)s; mountpoint: %(point)s" %
                  {'info': connection_info, 'name': instance['name'],
                   'point': mountpoint})

        if is_active:
            self._svc_driver.attach_volume_active(context, connection_info,
                                                  instance, mountpoint,
                                                  rollback)
        else:
            self._svc_driver.attach_volume_inactive(context, connection_info,
                                                    instance, mountpoint,
                                                    rollback)

    def detach_volume_from_instance(self, connection_info, instance,
                                    mountpoint, is_active, rollback=True):
        """Detach a volume from an instance."""

        if None in [connection_info, instance, is_active]:
            errmsg = _("Missing required parameters.")
            raise exception.ZVMDriverError(msg=errmsg)

        LOG.debug("Detach a volume from an instance. conn_info: %(info)s; " +
                    "instance: %(name)s; mountpoint: %(point)s" %
                  {'info': connection_info, 'name': instance['name'],
                   'point': mountpoint})

        if is_active:
            self._svc_driver.detach_volume_active(connection_info, instance,
                                                  mountpoint, rollback)
        else:
            self._svc_driver.detach_volume_inactive(connection_info, instance,
                                                    mountpoint, rollback)

    def get_volume_connector(self, instance):
        if not instance:
            errmsg = _("Instance must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)
        return self._svc_driver.get_volume_connector(instance)

    def has_persistent_volume(self, instance):
        if not instance:
            errmsg = _("Instance must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)
        return self._svc_driver.has_persistent_volume(instance)

    def extract_connection_info(self, context, connection_info):
        return self._svc_driver._extract_connection_info(context,
                                                         connection_info)

    def get_root_volume_connection_info(self, bdm, root_device):
        for bd in bdm:
            if zvmutils.is_volume_root(bd['mount_device'], root_device):
                return bd['connection_info']

        errmsg = _("Trying to extract volume connection info failed.")
        raise exception.ZVMDriverError(msg=errmsg)

    def volume_boot_init(self, instance, fcp):
        self._svc_driver.volume_boot_init(instance, fcp)

    def volume_boot_cleanup(self, instance, fcp):
        self._svc_driver.volume_boot_cleanup(instance, fcp)


@contextlib.contextmanager
def wrap_internal_errors():
    """Wrap internal exceptions to ZVMVolumeError."""

    try:
        yield
    except exception.ZVMBaseException:
        raise
    except Exception as err:
        raise exception.ZVMVolumeError(msg=err)


class DriverAPI(object):
    """DriverAPI for implement volume_attach on IBM z/VM platform."""

    def init_host(self, host_stats):
        """Initialize host environment."""
        raise NotImplementedError

    def get_volume_connector(self, instance):
        """Get volume connector for current driver."""
        raise NotImplementedError

    def attach_volume_active(self, context, connection_info, instance,
                             mountpoint, rollback):
        """Attach a volume to an running instance."""
        raise NotImplementedError

    def detach_volume_active(self, connection_info, instance, mountpoint,
                             rollback):
        """Detach a volume from an running instance."""
        raise NotImplementedError

    def attach_volume_inactive(self, context, connection_info, instance,
                               mountpoint, rollback):
        """Attach a volume to an shutdown instance."""
        raise NotImplementedError

    def detach_volume_inactive(self, connection_info, instance, mountpoint,
                               rollback):
        """Detach a volume from an shutdown instance."""
        raise NotImplementedError

    def has_persistent_volume(self, instance):
        """Decide if the specified instance has persistent volumes attached."""
        raise NotImplementedError


class SVCDriver(DriverAPI):
    """SVC volume operator on IBM z/VM platform."""

    def __init__(self):
        self._xcat_url = zvmutils.XCATUrl()
        self._path_utils = zvmutils.PathUtils()
        self._host = CONF.zvm_host
        self._pool_name = CONF.zvm_scsi_pool
        self._fcp_pool = set()
        self._instance_fcp_map = {}
        self._is_instance_fcp_map_locked = False
        self._volume_api = volume.API()

        self._actions = {'attach_volume': 'addScsiVolume',
                        'detach_volume': 'removeScsiVolume',
                        'create_mountpoint': 'createfilesysnode',
                        'remove_mountpoint': 'removefilesysnode'}

        self._RESERVE = 0
        self._INCREASE = 1
        self._DECREASE = 2
        self._REMOVE = 3

    def init_host(self, host_stats):
        """Initialize host environment."""

        if not host_stats:
            errmsg = _("Can not obtain host stats.")
            raise exception.ZVMDriverError(msg=errmsg)

        zhcp_fcp_list = CONF.zvm_zhcp_fcp_list
        fcp_devices = self._expand_fcp_list(zhcp_fcp_list)
        hcpnode = host_stats[0]['zhcp']['nodename']
        for _fcp in fcp_devices:
            with zvmutils.ignore_errors():
                self._attach_device(hcpnode, _fcp)
            with zvmutils.ignore_errors():
                self._online_device(hcpnode, _fcp)

        fcp_list = CONF.zvm_fcp_list
        if (fcp_list is None):
            errmsg = _("At least one fcp list should be given")
            LOG.error(errmsg)
            raise exception.ZVMVolumeError(msg=errmsg)
        self._init_fcp_pool(fcp_list)

    def _init_fcp_pool(self, fcp_list):
        """Map all instances and their fcp devices, and record all free fcps.
        One instance should use only one fcp device so far.
        """

        self._fcp_pool = self._expand_fcp_list(fcp_list)
        self._instance_fcp_map = {}
        # Any other functions should not modify _instance_fcp_map during
        # FCP pool initialization
        self._is_instance_fcp_map_locked = True

        compute_host_bdms = self._get_host_volume_bdms()
        for instance_bdms in compute_host_bdms:
            instance_name = instance_bdms['instance']['name']

            for _bdm in instance_bdms['instance_bdms']:
                connection_info = self._build_connection_info(_bdm)
                try:
                    _fcp = connection_info['data']['zvm_fcp']
                    if _fcp and _fcp in self._fcp_pool:
                        self._update_instance_fcp_map(instance_name, _fcp,
                                                      self._INCREASE)
                    if _fcp and _fcp not in self._fcp_pool:
                        errmsg = _("FCP device %(dev)s is not configured but "
                                   "is used by %(inst_name)s.") % {'dev': _fcp,
                                   'inst_name': instance_name}
                        LOG.warning(errmsg)
                except (TypeError, KeyError):
                    pass

        for _key in self._instance_fcp_map.keys():
            fcp = self._instance_fcp_map.get(_key)['fcp']
            self._fcp_pool.remove(fcp)
        self._is_instance_fcp_map_locked = False

    def _update_instance_fcp_map_if_unlocked(self, instance_name, fcp, action):
        while self._is_instance_fcp_map_locked:
            time.sleep(1)
        self._update_instance_fcp_map(instance_name, fcp, action)

    def _update_instance_fcp_map(self, instance_name, fcp, action):
        fcp = fcp.lower()
        if instance_name in self._instance_fcp_map:
            # One instance should use only one fcp device so far
            current_fcp = self._instance_fcp_map.get(instance_name)['fcp']
            if fcp != current_fcp:
                errmsg = _("Instance %(ins_name)s has multiple FCP devices "
                           "attached! FCP1: %(fcp1)s, FCP2: %(fcp2)s"
                           ) % {'ins_name': instance_name, 'fcp1': fcp,
                                'fcp2': current_fcp}
                LOG.warning(errmsg)
                return

        if action == self._RESERVE:
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                if count > 0:
                    errmsg = _("Try to reserve a fcp device which already "
                               "has volumes attached on: %(ins_name)s:%(fcp)s"
                               ) % {'ins_name': instance_name, 'fcp': fcp}
                    LOG.warning(errmsg)
            else:
                new_item = {instance_name: {'fcp': fcp, 'count': 0}}
                self._instance_fcp_map.update(new_item)

        elif action == self._INCREASE:
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                new_item = {instance_name: {'fcp': fcp, 'count': count + 1}}
                self._instance_fcp_map.update(new_item)
            else:
                new_item = {instance_name: {'fcp': fcp, 'count': 1}}
                self._instance_fcp_map.update(new_item)

        elif action == self._DECREASE:
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                if count > 0:
                    new_item = {instance_name: {'fcp': fcp,
                                                'count': count - 1}}
                    self._instance_fcp_map.update(new_item)
                else:
                    fcp = self._instance_fcp_map[instance_name]['fcp']
                    self._instance_fcp_map.pop(instance_name)
                    self._fcp_pool.add(fcp)
            else:
                errmsg = _("Try to decrease an inexistent map item: "
                           "%(ins_name)s:%(fcp)s"
                           ) % {'ins_name': instance_name, 'fcp': fcp}
                LOG.warning(errmsg)

        elif action == self._REMOVE:
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                if count > 0:
                    errmsg = _("Try to remove a map item while some volumes "
                               "are still attached on: %(ins_name)s:%(fcp)s"
                               ) % {'ins_name': instance_name, 'fcp': fcp}
                    LOG.warning(errmsg)
                else:
                    fcp = self._instance_fcp_map[instance_name]['fcp']
                    self._instance_fcp_map.pop(instance_name)
                    self._fcp_pool.add(fcp)
            else:
                errmsg = _("Try to remove an inexistent map item: "
                           "%(ins_name)s:%(fcp)s"
                           ) % {'ins_name': instance_name, 'fcp': fcp}
                LOG.warning(errmsg)

        else:
            errmsg = _("Unrecognized option: %s") % action
            LOG.warning(errmsg)

    def _get_host_volume_bdms(self):
        """Return all block device mappings on a compute host."""

        compute_host_bdms = []
        instances = self._get_all_instances()
        for instance in instances:
            instance_bdms = self._get_instance_bdms(instance)
            compute_host_bdms.append(dict(instance=instance,
                                          instance_bdms=instance_bdms))

        return compute_host_bdms

    def _get_all_instances(self):
        context = nova.context.get_admin_context()
        return instance_obj.InstanceList.get_by_host(context, self._host)

    def _get_instance_bdms(self, instance):
        context = nova.context.get_admin_context()
        instance_bdms = [bdm for bdm in
                             (block_device_obj.BlockDeviceMappingList.
                              get_by_instance_uuid(context, instance['uuid']))
                             if bdm.is_volume]
        return instance_bdms

    def has_persistent_volume(self, instance):
        return bool(self._get_instance_bdms(instance))

    def _build_connection_info(self, bdm):
        try:
            connection_info = jsonutils.loads(bdm['connection_info'])
            return connection_info
        except (TypeError, KeyError, ValueError):
            return None

    def get_volume_connector(self, instance):
        empty_connector = {'zvm_fcp': None, 'wwpns': [], 'host': ''}

        try:
            fcp = self._instance_fcp_map.get(instance['name'])['fcp']
        except Exception:
            fcp = None
        if not fcp:
            fcp = self._get_fcp_from_pool()
            if fcp:
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp, self._RESERVE)

        if not fcp:
            errmsg = _("No available FCP device found.")
            LOG.warning(errmsg)
            return empty_connector
        fcp = fcp.lower()

        wwpn = self._get_wwpn(fcp)
        if not wwpn:
            errmsg = _("FCP device %s has no available WWPN.") % fcp
            LOG.warning(errmsg)
            return empty_connector
        wwpn = wwpn.lower()

        return {'zvm_fcp': fcp, 'wwpns': [wwpn], 'host': CONF.zvm_host}

    def _get_wwpn(self, fcp):
        states = ['active', 'free']
        for _state in states:
            fcps_info = self._list_fcp_details(_state)
            if not fcps_info:
                continue
            wwpn = self._extract_wwpn_from_fcp_info(fcp, fcps_info)
            if wwpn:
                return wwpn

    def _list_fcp_details(self, state):
        fields = '&field=--fcpdevices&field=' + state + '&field=details'
        rsp = self._xcat_rinv(fields)
        try:
            fcp_details = rsp['info'][0][0].splitlines()
            return fcp_details
        except (TypeError, KeyError):
            return None

    def _extract_wwpn_from_fcp_info(self, fcp, fcps_info):
        """The FCP infomation would look like this:
           host: FCP device number: xxxx
           host:   Status: Active
           host:   NPIV world wide port number: xxxxxxxx
           host:   Channel path ID: xx
           host:   Physical world wide port number: xxxxxxxx
           ......
           host: FCP device number: xxxx
           host:   Status: Active
           host:   NPIV world wide port number: xxxxxxxx
           host:   Channel path ID: xx
           host:   Physical world wide port number: xxxxxxxx

        """

        lines_per_item = 5
        num_fcps = len(fcps_info) / lines_per_item
        fcp = fcp.upper()
        for _cur in range(0, num_fcps):
            # Find target FCP device
            if fcp not in fcps_info[_cur * lines_per_item]:
                continue
            # Try to get NPIV WWPN first
            wwpn_info = fcps_info[(_cur + 1) * lines_per_item - 3]
            wwpn = self._get_wwpn_from_line(wwpn_info)
            if not wwpn:
                # Get physical WWPN if NPIV WWPN is none
                wwpn_info = fcps_info[(_cur + 1) * lines_per_item - 1]
                wwpn = self._get_wwpn_from_line(wwpn_info)
            return wwpn

    def _get_wwpn_from_line(self, info_line):
        wwpn = info_line.split(':')[-1].strip()
        if wwpn and wwpn.upper() != 'NONE':
            return wwpn
        else:
            return None

    def _get_fcp_from_pool(self):
        if self._fcp_pool:
            return self._fcp_pool.pop()

        self._init_fcp_pool(CONF.zvm_fcp_list)
        if self._fcp_pool:
            return self._fcp_pool.pop()

    def _extract_connection_info(self, context, connection_info):
        with wrap_internal_errors():
            LOG.debug("Extract connection_info: %s" % connection_info)

            lun = connection_info['data']['target_lun']
            lun = "%04x000000000000" % int(lun)
            wwpn = connection_info['data']['target_wwn']
            size = '0G'
            # There is no context in detach case
            if context:
                volume_id = connection_info['data']['volume_id']
                volume = self._get_volume_by_id(context, volume_id)
                size = str(volume['size']) + 'G'
            fcp = connection_info['data']['zvm_fcp']

            return (lun.lower(), self._format_wwpn(wwpn), size, fcp.lower())

    def _format_wwpn(self, wwpn):
        if isinstance(wwpn, basestring):
            return wwpn.lower()
        else:
            new_wwpn = ';'.join(wwpn)
            return new_wwpn.lower()

    def _get_volume_by_id(self, context, volume_id):
        volume = self._volume_api.get(context, volume_id)
        return volume

    def attach_volume_active(self, context, connection_info, instance,
                             mountpoint, rollback=True):
        """Attach a volume to an running instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(context,
                                                               connection_info)
        try:
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._INCREASE)
            self._add_zfcp_to_pool(fcp, wwpn, lun, size)
            self._add_zfcp(instance, fcp, wwpn, lun, size)
            if mountpoint:
                self._create_mountpoint(instance, fcp, wwpn, lun, mountpoint)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._DECREASE)
            do_detach = not self._is_fcp_in_use(instance, fcp)
            if rollback:
                with zvmutils.ignore_errors():
                    self._remove_mountpoint(instance, mountpoint)
                with zvmutils.ignore_errors():
                    self._remove_zfcp(instance, fcp, wwpn, lun)
                with zvmutils.ignore_errors():
                    self._remove_zfcp_from_pool(wwpn, lun)
                with zvmutils.ignore_errors():
                    if do_detach:
                        self._detach_device(instance['name'], fcp)
            raise

    def detach_volume_active(self, connection_info, instance, mountpoint,
                             rollback=True):
        """Detach a volume from an running instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(None,
                                                               connection_info)
        try:
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._DECREASE)
            do_detach = not self._is_fcp_in_use(instance, fcp)
            if mountpoint:
                self._remove_mountpoint(instance, mountpoint)
            self._remove_zfcp(instance, fcp, wwpn, lun)
            self._remove_zfcp_from_pool(wwpn, lun)
            if do_detach:
                self._detach_device(instance['name'], fcp)
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp, self._REMOVE)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._INCREASE)
            if rollback:
                with zvmutils.ignore_errors():
                    self._add_zfcp_to_pool(fcp, wwpn, lun, size)
                with zvmutils.ignore_errors():
                    self._add_zfcp(instance, fcp, wwpn, lun, size)
                if mountpoint:
                    with zvmutils.ignore_errors():
                        self._create_mountpoint(instance, fcp, wwpn, lun,
                                                mountpoint)
            raise

    def attach_volume_inactive(self, context, connection_info, instance,
                               mountpoint, rollback=True):
        """Attach a volume to an shutdown instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(context,
                                                               connection_info)
        try:
            do_attach = not self._is_fcp_in_use(instance, fcp)
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._INCREASE)
            self._add_zfcp_to_pool(fcp, wwpn, lun, size)
            self._allocate_zfcp(instance, fcp, size, wwpn, lun)
            self._notice_attach(instance, fcp, wwpn, lun, mountpoint)
            if do_attach:
                self._attach_device(instance['name'], fcp)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._DECREASE)
            do_detach = not self._is_fcp_in_use(instance, fcp)
            if rollback:
                with zvmutils.ignore_errors():
                    self._notice_detach(instance, fcp, wwpn, lun, mountpoint)
                with zvmutils.ignore_errors():
                    self._remove_zfcp_from_pool(wwpn, lun)
                with zvmutils.ignore_errors():
                    if do_detach:
                        self._detach_device(instance['name'], fcp)
                        self._update_instance_fcp_map_if_unlocked(
                                instance['name'], fcp, self._REMOVE)
            raise

    def detach_volume_inactive(self, connection_info, instance, mountpoint,
                               rollback=True):
        """Detach a volume from an shutdown instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(None,
                                                               connection_info)
        try:
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._DECREASE)
            do_detach = not self._is_fcp_in_use(instance, fcp)
            self._remove_zfcp(instance, fcp, wwpn, lun)
            self._remove_zfcp_from_pool(wwpn, lun)
            self._notice_detach(instance, fcp, wwpn, lun, mountpoint)
            if do_detach:
                self._detach_device(instance['name'], fcp)
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp, self._REMOVE)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                      self._INCREASE)
            if rollback:
                with zvmutils.ignore_errors():
                    self._attach_device(instance['name'], fcp)
                with zvmutils.ignore_errors():
                    self._notice_attach(instance, fcp, wwpn, lun, mountpoint)
                with zvmutils.ignore_errors():
                    self._add_zfcp_to_pool(fcp, wwpn, lun, size)
                with zvmutils.ignore_errors():
                    self._allocate_zfcp(instance, fcp, size, wwpn, lun)
            raise

    def volume_boot_init(self, instance, fcp):
        self._update_instance_fcp_map_if_unlocked(instance['name'], fcp,
                                                  self._INCREASE)
        self._attach_device(instance['name'], fcp)

    def volume_boot_cleanup(self, instance, fcp):
        self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                  fcp, self._DECREASE)
        self._detach_device(instance['name'], fcp)

    def _expand_fcp_list(self, fcp_list):
        """Expand fcp list string into a python list object which contains
        each fcp devices in the list string. A fcp list is composed of fcp
        device addresses, range indicator '-', and split indicator ';'.

        For example, if fcp_list is
        "0011-0013;0015;0017-0018", expand_fcp_list(fcp_list) will return
        [0011, 0012, 0013, 0015, 0017, 0018].

        """

        LOG.debug("Expand FCP list %s" % fcp_list)

        if not fcp_list:
            return set()

        range_pattern = '[0-9a-fA-F]{1,4}(-[0-9a-fA-F]{1,4})?'
        match_pattern = "^(%(range)s)(;%(range)s)*$" % {'range': range_pattern}
        if not re.match(match_pattern, fcp_list):
            errmsg = _("Invalid FCP address %s") % fcp_list
            raise exception.ZVMDriverError(msg=errmsg)

        fcp_devices = set()
        for _range in fcp_list.split(';'):
            if '-' not in _range:
                # single device
                fcp_addr = int(_range, 16)
                fcp_devices.add("%04x" % fcp_addr)
            else:
                # a range of address
                (_min, _max) = _range.split('-')
                _min = int(_min, 16)
                _max = int(_max, 16)
                for fcp_addr in range(_min, _max + 1):
                    fcp_devices.add("%04x" % fcp_addr)

        # remove duplicate entries
        return fcp_devices

    def _attach_device(self, node, addr, mode='0'):
        """Attach a device to a node."""

        body = [' '.join(['--dedicatedevice', addr, addr, mode])]
        self._xcat_chvm(node, body)

    def _detach_device(self, node, vdev):
        """Detach a device from a node."""

        body = [' '.join(['--undedicatedevice', vdev])]
        self._xcat_chvm(node, body)

    def _online_device(self, node, dev):
        """After attaching a device to a node, the device should be made
        online before it being in use.

        """

        body = ["command=cio_ignore -r %s" % dev]
        self._xcat_xdsh(node, body)

        body = ["command=chccwdev -e %s" % dev]
        self._xcat_xdsh(node, body)

    def _is_fcp_in_use(self, instance, fcp):
        if instance['name'] in self._instance_fcp_map:
            count = self._instance_fcp_map.get(instance['name'])['count']
            if count > 0:
                return True
        return False

    def _notice_attach(self, instance, fcp, wwpn, lun, mountpoint):
        # Create and send volume file
        action = self._actions['attach_volume']
        parms = self._get_volume_parms(action, fcp, wwpn, lun)
        self._send_notice(instance, parms)

        # Create and send mount point file
        action = self._actions['create_mountpoint']
        parms = self._get_mountpoint_parms(action, fcp, wwpn, lun, mountpoint)
        self._send_notice(instance, parms)

    def _notice_detach(self, instance, fcp, wwpn, lun, mountpoint):
        # Create and send volume file
        action = self._actions['detach_volume']
        parms = self._get_volume_parms(action, fcp, wwpn, lun)
        self._send_notice(instance, parms)

        # Create and send mount point file
        action = self._actions['remove_mountpoint']
        parms = self._get_mountpoint_parms(action, fcp, wwpn, lun, mountpoint)
        self._send_notice(instance, parms)

    def _get_volume_parms(self, action, fcp, wwpn, lun):
        action = "action=%s" % action
        fcp = "fcpAddr=%s" % fcp
        # Replace the ; in wwpn to be , in invoke script file since shell will
        # treat ; as a new line
        wwpn = wwpn.replace(';', ',')
        wwpn = "wwpn=%s" % wwpn
        lun = "lun=%s" % lun
        parmline = ' '.join([action, fcp, wwpn, lun])
        return parmline

    def _get_mountpoint_parms(self, action, fcp, wwpn, lun, mountpoint):
        action_parm = "action=%s" % action
        mountpoint = "tgtFile=%s" % mountpoint
        wwpn = wwpn.replace(';', ',')
        if action == self._actions['create_mountpoint']:
            path = self._get_zfcp_path_pattern()
            srcdev = path % {'fcp': fcp, 'wwpn': wwpn, 'lun': lun}
            srcfile = "srcFile=%s" % srcdev
            parmline = ' '.join([action_parm, mountpoint, srcfile])
        else:
            parmline = ' '.join([action_parm, mountpoint])
        return parmline

    def _send_notice(self, instance, parms):
        zvmutils.aemod_handler(instance['name'], const.DISK_FUNC_NAME, parms)

    def _add_zfcp_to_pool(self, fcp, wwpn, lun, size):
        body = [' '.join(['--addzfcp2pool', self._pool_name, 'free', wwpn,
                          lun, size, fcp])]
        self._xcat_chhy(body)

    def _remove_zfcp_from_pool(self, wwpn, lun):
        body = [' '.join(['--removezfcpfrompool', CONF.zvm_scsi_pool, lun,
                          wwpn])]
        self._xcat_chhy(body)

    def _add_zfcp(self, instance, fcp, wwpn, lun, size):
        body = [' '.join(['--addzfcp', CONF.zvm_scsi_pool, fcp, str(0), size,
                          str(0), wwpn, lun])]
        self._xcat_chvm(instance['name'], body)

    def _remove_zfcp(self, instance, fcp, wwpn, lun):
        body = [' '.join(['--removezfcp', fcp, wwpn, lun, '1'])]
        self._xcat_chvm(instance['name'], body)

    def _create_mountpoint(self, instance, fcp, wwpn, lun, mountpoint):
        path = self._get_zfcp_path_pattern()
        srcdev = path % {'fcp': fcp, 'wwpn': wwpn, 'lun': lun}
        body = [" ".join(['--createfilesysnode', srcdev, mountpoint])]
        self._xcat_chvm(instance['name'], body)

    def _remove_mountpoint(self, instance, mountpoint):
        body = [' '.join(['--removefilesysnode', mountpoint])]
        self._xcat_chvm(instance['name'], body)

    def _allocate_zfcp(self, instance, fcp, size, wwpn, lun):
        body = [" ".join(['--reservezfcp', CONF.zvm_scsi_pool, 'used',
                          instance['name'], fcp, size, wwpn, lun])]
        self._xcat_chhy(body)

    def _xcat_chvm(self, node, body):
        url = self._xcat_url.chvm('/' + node)
        zvmutils.xcat_request('PUT', url, body)

    def _xcat_chhy(self, body):
        url = self._xcat_url.chhv('/' + self._host)
        zvmutils.xcat_request('PUT', url, body)

    def _xcat_xdsh(self, node, body):
        url = self._xcat_url.xdsh('/' + node)
        zvmutils.xcat_request('PUT', url, body)

    def _xcat_rinv(self, fields):
        url = self._xcat_url.rinv('/' + self._host, fields)
        return zvmutils.xcat_request('GET', url)

    def _get_zfcp_path_pattern(self):
        return '/dev/disk/by-path/ccw-0.0.%(fcp)s-zfcp-0x%(wwpn)s:0x%(lun)s'
