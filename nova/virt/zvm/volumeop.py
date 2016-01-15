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
import six
import time

from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_utils import excutils

import nova.context
from nova.i18n import _, _LW
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

    _svc_driver_obj = None

    def __init__(self):
        if not VolumeOperator._svc_driver_obj:
            VolumeOperator._svc_driver_obj = SVCDriver()
        self._svc_driver = VolumeOperator._svc_driver_obj

    def init_host(self, host_stats):
        try:
            self._svc_driver.init_host(host_stats)
        except (exception.ZVMDriverError, exception.ZVMVolumeError) as err:
            LOG.warning(_LW("Initialize zhcp failed. Reason: %s") %
                        err.format_message())

    def attach_volume_to_instance(self, context, connection_info, instance,
                                  mountpoint, is_active, rollback=True):
        """Attach a volume to an instance."""

        if not connection_info:
            errmsg = _("Missing required parameters: connection_info.")
            raise exception.ZVMDriverError(msg=errmsg)

        if not instance:
            errmsg = _("Missing required parameters: instance.")
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

        if not connection_info:
            errmsg = _("Missing required parameters: connection_info.")
            raise exception.ZVMDriverError(msg=errmsg)

        if not instance:
            errmsg = _("Missing required parameters: instance.")
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
        if not connection_info:
            errmsg = _("Connection_info must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)
        return self._svc_driver._extract_connection_info(context,
                                                         connection_info)

    def get_root_volume_connection_info(self, bdm_list, root_device):
        if not bdm_list:
            errmsg = _("Block_device_mappings must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)

        if not root_device:
            errmsg = _("root_device must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)

        for bdm in bdm_list:
            if zvmutils.is_volume_root(bdm.mount_device, root_device):
                return bdm.connection_info
        errmsg = _("Failed to get connection info of root volume.")
        raise exception.ZVMDriverError(msg=errmsg)

    def volume_boot_init(self, instance, fcp):
        if not instance:
            errmsg = _("instance must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)
        if not fcp:
            errmsg = _("fcp must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)
        self._svc_driver.volume_boot_init(instance, fcp)

    def volume_boot_cleanup(self, instance, fcp):
        if not instance:
            errmsg = _("instance must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)
        if not fcp:
            errmsg = _("fcp must be provided.")
            raise exception.ZVMDriverError(msg=errmsg)
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

    class FCP(object):
        """FCP adapter class."""

        _DEV_NO_PATTERN = '[0-9a-f]{1,4}'
        _WWPN_PATTERN = '[0-9a-f]{16}'
        _CHPID_PATTERN = '[0-9A-F]{2}'

        def __init__(self, init_info):
            """Initialize a FCP device object from several lines of string
               describing properties of the FCP device.
               Here is a sample:
                   opnstk1: FCP device number: B83D
                   opnstk1:   Status: Free
                   opnstk1:   NPIV world wide port number: NONE
                   opnstk1:   Channel path ID: 59
                   opnstk1:   Physical world wide port number: 20076D8500005181
               The format comes from the response of xCAT, do not support
               arbitrary format.

            """

            self._dev_no = None
            self._npiv_port = None
            self._chpid = None
            self._physical_port = None

            self._is_valid = True
            # Sometimes nova issues get_volume_connector() for some reasons,
            # which requires a FCP device being assigned to the instance. But
            # nova will not attach any volume to the instance later. In this
            # case we need to release the FCP device in later time. So a FCP
            # device which is assigned to an instance doesn't means it's
            # actually used by the instance.
            self._is_reserved = False
            self._is_in_use = False
            self._reserve_time = 0

            if isinstance(init_info, list) and (len(init_info) == 5):
                self._dev_no = self._get_dev_number_from_line(init_info[0])
                self._npiv_port = self._get_wwpn_from_line(init_info[2])
                self._chpid = self._get_chpid_from_line(init_info[3])
                self._physical_port = self._get_wwpn_from_line(init_info[4])
            self._validate_device()

        def _get_wwpn_from_line(self, info_line):
            wwpn = info_line.split(':')[-1].strip().lower()
            return wwpn if (wwpn and wwpn.upper() != 'NONE') else None

        def _get_dev_number_from_line(self, info_line):
            dev_no = info_line.split(':')[-1].strip().lower()
            return dev_no if dev_no else None

        def _get_chpid_from_line(self, info_line):
            chpid = info_line.split(':')[-1].strip().upper()
            return chpid if chpid else None

        def _validate_device(self):
            if not (self._dev_no and self._chpid):
                self._is_valid = False
                return
            if not (self._npiv_port or self._physical_port):
                self._is_valid = False
                return
            if not (re.match(self._DEV_NO_PATTERN, self._dev_no) and
                    re.match(self._CHPID_PATTERN, self._chpid)):
                self._is_valid = False
                return
            if self._npiv_port and not re.match(self._WWPN_PATTERN,
                                                self._npiv_port):
                self._is_valid = False
                return
            if self._physical_port and not re.match(self._WWPN_PATTERN,
                                                    self._physical_port):
                self._is_valid = False
                return

        def is_valid(self):
            return self._is_valid

        def get_dev_no(self):
            return self._dev_no

        def get_npiv_port(self):
            return self._npiv_port

        def get_chpid(self):
            return self._chpid

        def get_physical_port(self):
            return self._physical_port

        def reserve_device(self):
            self._is_reserved = True
            self._is_in_use = False
            self._reserve_time = time.time()

        def is_reserved(self):
            return self._is_reserved

        def set_in_use(self):
            self._is_reserved = True
            self._is_in_use = True

        def is_in_use(self):
            return self._is_in_use

        def release_device(self):
            self._is_reserved = False
            self._is_in_use = False
            self._reserve_time = 0

        def get_reserve_time(self):
            return self._reserve_time

    _RESERVE = 0
    _INCREASE = 1
    _DECREASE = 2
    _REMOVE = 3

    def __init__(self):
        self._xcat_url = zvmutils.XCATUrl()
        self._path_utils = zvmutils.PathUtils()
        self._host = CONF.zvm_host
        self._pool_name = CONF.zvm_scsi_pool
        self._fcp_pool = {}
        self._instance_fcp_map = {}
        self._is_instance_fcp_map_locked = False
        self._volume_api = volume.API()

        self._actions = {'attach_volume': 'addScsiVolume',
                        'detach_volume': 'removeScsiVolume',
                        'create_mountpoint': 'createfilesysnode',
                        'remove_mountpoint': 'removefilesysnode'}

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
        self._init_instance_fcp_map(fcp_list)

    def _init_fcp_pool(self, fcp_list):
        """The FCP infomation looks like this:
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
        complete_fcp_set = self._expand_fcp_list(fcp_list)
        fcp_info = self._get_all_fcp_info()
        lines_per_item = 5
        num_fcps = len(fcp_info) / lines_per_item
        for n in range(0, num_fcps):
            fcp_init_info = fcp_info[(5 * n):(5 * (n + 1))]
            fcp = SVCDriver.FCP(fcp_init_info)
            dev_no = fcp.get_dev_no()
            if dev_no in complete_fcp_set:
                if fcp.is_valid():
                    self._fcp_pool[dev_no] = fcp
                else:
                    errmsg = _("Find an invalid FCP device with properties {"
                               "dev_no: %(dev_no)s, "
                               "NPIV_port: %(NPIV_port)s, "
                               "CHPID: %(CHPID)s, "
                               "physical_port: %(physical_port)s} !") % {
                                'dev_no': fcp.get_dev_no(),
                                'NPIV_port': fcp.get_npiv_port(),
                                'CHPID': fcp.get_chpid(),
                                'physical_port': fcp.get_physical_port()}
                    LOG.warning(errmsg)

    def _get_all_fcp_info(self):
        fcp_info = []
        free_fcp_info = self._list_fcp_details('free')
        active_fcp_info = self._list_fcp_details('active')
        if free_fcp_info:
            fcp_info.extend(free_fcp_info)
        if active_fcp_info:
            fcp_info.extend(active_fcp_info)
        return fcp_info

    def _init_instance_fcp_map(self, fcp_list):
        """Map all instances and their fcp devices.
           One instance should use only one fcp device when multipath
           is not enabled.

        """

        complete_fcp_set = self._expand_fcp_list(fcp_list)
        # All other functions should not modify _instance_fcp_map during
        # FCP pool initialization
        self._is_instance_fcp_map_locked = True

        compute_host_bdms = self._get_host_volume_bdms()
        for instance_bdms in compute_host_bdms:
            instance_name = instance_bdms['instance']['name']

            for _bdm in instance_bdms['instance_bdms']:
                connection_info = self._build_connection_info(_bdm)
                try:
                    # Remove invalid FCP devices
                    fcp_list = connection_info['data']['zvm_fcp']
                    invalid_fcp_list = []
                    for fcp in fcp_list:
                        if fcp not in complete_fcp_set:
                            errmsg = _("FCP device %(dev)s is not configured "
                                       "but is used by %(inst_name)s.") % {
                                        'dev': fcp, 'inst_name': instance_name}
                            LOG.warning(errmsg)
                            invalid_fcp_list.append(fcp)
                    for fcp in invalid_fcp_list:
                        fcp_list.remove(fcp)
                    # Map valid FCP devices
                    if fcp_list:
                        self._update_instance_fcp_map(instance_name, fcp_list,
                                                      self._INCREASE)
                except (TypeError, KeyError):
                    pass

        self._is_instance_fcp_map_locked = False

    def _update_instance_fcp_map_if_unlocked(self, instance_name, fcp_list,
                                             action):
        while self._is_instance_fcp_map_locked:
            time.sleep(1)
        self._update_instance_fcp_map(instance_name, fcp_list, action)

    def _update_instance_fcp_map(self, instance_name, fcp_list, action):
        if instance_name in self._instance_fcp_map:
            current_list = self._instance_fcp_map[instance_name]['fcp_list']
            if fcp_list and (fcp_list != current_list):
                errmsg = _("FCP conflict for instance %(ins_name)s! "
                           "Original set: %(origin)s, new set: %(new)s"
                           ) % {'ins_name': instance_name,
                                'origin': current_list, 'new': fcp_list}
                raise exception.ZVMVolumeError(msg=errmsg)

        if action == self._RESERVE:
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                if count > 0:
                    errmsg = _("Try to reserve fcp devices on which there are "
                               "already volumes attached: "
                               "%(ins_name)s:%(fcp_list)s") % {
                                'ins_name': instance_name,
                                'fcp_list': fcp_list}
                    raise exception.ZVMVolumeError(msg=errmsg)
            else:
                for fcp_no in fcp_list:
                    fcp = self._fcp_pool.get(fcp_no)
                    fcp.reserve_device()
                new_item = {instance_name: {'fcp_list': fcp_list, 'count': 0}}
                self._instance_fcp_map.update(new_item)

        elif action == self._INCREASE:
            for fcp_no in fcp_list:
                fcp = self._fcp_pool.get(fcp_no)
                fcp.set_in_use()
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                new_item = {instance_name: {'fcp_list': fcp_list,
                                            'count': count + 1}}
                self._instance_fcp_map.update(new_item)
            else:
                new_item = {instance_name: {'fcp_list': fcp_list, 'count': 1}}
                self._instance_fcp_map.update(new_item)

        elif action == self._DECREASE:
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                if count > 0:
                    new_item = {instance_name: {'fcp_list': fcp_list,
                                                'count': count - 1}}
                    self._instance_fcp_map.update(new_item)
                    if count == 1:
                        for fcp_no in fcp_list:
                            fcp = self._fcp_pool.get(fcp_no)
                            # The function 'reserve_device()' will do two jobs.
                            # The first one is to cancel the 'in_use' status of
                            # the FCP, so function _is_fcp_in_use() can return
                            # right result. The second one is to facilitate
                            # rollback process in case the FCP being assigned
                            # to another instance after it's released.
                            fcp.reserve_device()
                else:
                    errmsg = _("Reference count falling down below 0 for map "
                               "item: %(ins_name)s:%(fcp_list)s") % {
                                'ins_name': instance_name,
                                'fcp_list': fcp_list}
                    raise exception.ZVMVolumeError(msg=errmsg)
            else:
                errmsg = _("Try to decrease an inexistent map item: "
                           "%(ins_name)s:%(fcp_list)s"
                           ) % {'ins_name': instance_name,
                                'fcp_list': fcp_list}
                raise exception.ZVMVolumeError(msg=errmsg)

        elif action == self._REMOVE:
            if instance_name in self._instance_fcp_map:
                count = self._instance_fcp_map[instance_name]['count']
                if count > 0:
                    errmsg = _("Try to remove a map item with volumes "
                               "attached on: %(ins_name)s:%(fcp_list)s"
                               ) % {'ins_name': instance_name,
                                    'fcp_list': fcp_list}
                    raise exception.ZVMVolumeError(msg=errmsg)
                else:
                    for fcp_no in fcp_list:
                        fcp = self._fcp_pool.get(fcp_no)
                        fcp.release_device()
                    self._instance_fcp_map.pop(instance_name)
            else:
                errmsg = _("Try to remove an inexistent map item: "
                           "%(ins_name)s:%(fcp_list)s"
                           ) % {'ins_name': instance_name,
                                'fcp_list': fcp_list}
                raise exception.ZVMVolumeError(msg=errmsg)

        else:
            errmsg = _("Unrecognized option: %s") % action
            raise exception.ZVMVolumeError(msg=errmsg)

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
        except (TypeError, KeyError, ValueError):
            return None

        # The value of zvm_fcp is a string in former release. It will be set
        # in future. We have to translate a string FCP to a single-element set
        # in order to make code goes on.
        fcp = connection_info['data']['zvm_fcp']
        if fcp and isinstance(fcp, six.string_types):
            connection_info['data']['zvm_fcp'] = [fcp]

        return connection_info

    def get_volume_connector(self, instance):
        empty_connector = {'zvm_fcp': [], 'wwpns': [], 'host': ''}

        try:
            fcp_list = self._instance_fcp_map.get(instance['name'])['fcp_list']
        except Exception:
            fcp_list = []
        if not fcp_list:
            fcp_list = self._get_fcp_from_pool()
            if fcp_list:
                self._update_instance_fcp_map_if_unlocked(
                        instance['name'], fcp_list, self._RESERVE)

        if not fcp_list:
            errmsg = _("No available FCP device found.")
            LOG.warning(errmsg)
            return empty_connector

        wwpns = []
        for fcp_no in fcp_list:
            wwpn = self._get_wwpn(fcp_no)
            if not wwpn:
                errmsg = _("FCP device %s has no available WWPN.") % fcp_no
                LOG.warning(errmsg)
            else:
                wwpns.append(wwpn)
        if not wwpns:
            errmsg = _("No available WWPN found.")
            LOG.warning(errmsg)
            return empty_connector

        return {'zvm_fcp': fcp_list, 'wwpns': wwpns, 'host': CONF.zvm_host}

    def _get_wwpn(self, fcp_no):
        fcp = self._fcp_pool.get(fcp_no)
        if not fcp:
            return None
        if fcp.get_npiv_port():
            return fcp.get_npiv_port()
        if fcp.get_physical_port():
            return fcp.get_physical_port()
        return None

    def _list_fcp_details(self, state):
        fields = '&field=--fcpdevices&field=' + state + '&field=details'
        rsp = self._xcat_rinv(fields)
        try:
            fcp_details = rsp['info'][0][0].splitlines()
            return fcp_details
        except (TypeError, KeyError):
            return None

    def _get_fcp_from_pool(self):
        fcp_list = []
        for fcp in list(self._fcp_pool.values()):
            if not fcp.is_reserved():
                fcp_list.append(fcp.get_dev_no())
                break

        if not fcp_list:
            self._release_fcps_reserved()
            for fcp in list(self._fcp_pool.values()):
                if not fcp.is_reserved():
                    fcp_list.append(fcp.get_dev_no())
                    break

        if not fcp_list:
            return []

        if not CONF.zvm_multiple_fcp:
            return fcp_list

        primary_fcp = self._fcp_pool.get(fcp_list.pop())
        backup_fcp = None
        for fcp in list(self._fcp_pool.values()):
            if not fcp.is_reserved() and (
                    fcp.get_chpid() != primary_fcp.get_chpid()):
                backup_fcp = fcp
                break

        fcp_list.append(primary_fcp.get_dev_no())
        if backup_fcp:
            fcp_list.append(backup_fcp.get_dev_no())
        else:
            errmsg = _("Can not find a backup FCP device for primary FCP "
                       "device %s") % primary_fcp.get_dev_no()
            LOG.warning(errmsg)
            return []
        return fcp_list

    def _release_fcps_reserved(self):
        current = time.time()
        for instance in list(self._instance_fcp_map.keys()):
            if self._instance_fcp_map.get(instance)['count'] != 0:
                continue
            # Only release FCP devices which are reserved more than 30 secs
            # in case concurrent assignment.
            fcp_list = self._instance_fcp_map.get(instance)['fcp_list']
            fcp = self._fcp_pool.get(fcp_list[0])
            if current - fcp.get_reserve_time() > 30:
                self._update_instance_fcp_map_if_unlocked(instance, fcp_list,
                                                          self._REMOVE)

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
            fcp_list = connection_info['data']['zvm_fcp']

            return (lun.lower(), self._format_wwpn(wwpn), size,
                    self._format_fcp_list(fcp_list))

    def _format_wwpn(self, wwpn):
        if isinstance(wwpn, six.string_types):
            return wwpn.lower()
        else:
            new_wwpn = ';'.join(wwpn)
            return new_wwpn.lower()

    def _format_fcp_list(self, fcp_list):
        if len(fcp_list) == 1:
            return fcp_list[0].lower()
        else:
            return ';'.join(fcp_list).lower()

    def _get_volume_by_id(self, context, volume_id):
        volume = self._volume_api.get(context, volume_id)
        return volume

    def attach_volume_active(self, context, connection_info, instance,
                             mountpoint, rollback=True):
        """Attach a volume to an running instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(context,
                                                               connection_info)
        fcp_list = fcp.split(';')
        self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                  fcp_list, self._INCREASE)
        try:
            self._add_zfcp_to_pool(fcp, wwpn, lun, size)
            self._add_zfcp(instance, fcp, wwpn, lun, size)
            if mountpoint:
                self._create_mountpoint(instance, fcp, wwpn, lun, mountpoint)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            with excutils.save_and_reraise_exception():
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp_list,
                                                          self._DECREASE)
                do_detach = not self._is_fcp_in_use(instance)
                if rollback:
                    with zvmutils.ignore_errors():
                        self._remove_mountpoint(instance, mountpoint)
                    with zvmutils.ignore_errors():
                        self._remove_zfcp(instance, fcp, wwpn, lun)
                    with zvmutils.ignore_errors():
                        self._remove_zfcp_from_pool(wwpn, lun)
                    with zvmutils.ignore_errors():
                        if do_detach:
                            for dev_no in fcp_list:
                                self._detach_device(instance['name'], dev_no)
                            self._update_instance_fcp_map_if_unlocked(
                                    instance['name'], fcp_list, self._REMOVE)

    def detach_volume_active(self, connection_info, instance, mountpoint,
                             rollback=True):
        """Detach a volume from an running instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(None,
                                                               connection_info)
        fcp_list = fcp.split(';')
        self._update_instance_fcp_map_if_unlocked(instance['name'], fcp_list,
                                                  self._DECREASE)
        try:
            do_detach = not self._is_fcp_in_use(instance)
            if mountpoint:
                self._remove_mountpoint(instance, mountpoint)
            self._remove_zfcp(instance, fcp, wwpn, lun)
            self._remove_zfcp_from_pool(wwpn, lun)
            if do_detach:
                for dev_no in fcp_list:
                    self._detach_device(instance['name'], dev_no)
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp_list,
                                                          self._REMOVE)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            with excutils.save_and_reraise_exception():
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp_list,
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

    def attach_volume_inactive(self, context, connection_info, instance,
                               mountpoint, rollback=True):
        """Attach a volume to an shutdown instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(context,
                                                               connection_info)
        fcp_list = fcp.split(';')
        do_attach = not self._is_fcp_in_use(instance)
        self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                  fcp_list, self._INCREASE)
        try:
            self._add_zfcp_to_pool(fcp, wwpn, lun, size)
            self._allocate_zfcp(instance, fcp, size, wwpn, lun)
            self._notice_attach(instance, fcp, wwpn, lun, mountpoint)
            if do_attach:
                for dev_no in fcp_list:
                    self._attach_device(instance['name'], dev_no)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            with excutils.save_and_reraise_exception():
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp_list,
                                                          self._DECREASE)
                if rollback:
                    with zvmutils.ignore_errors():
                        self._notice_detach(instance, fcp, wwpn, lun,
                                            mountpoint)
                    with zvmutils.ignore_errors():
                        self._remove_zfcp_from_pool(wwpn, lun)
                    with zvmutils.ignore_errors():
                        if do_attach:
                            for dev_no in fcp_list:
                                self._detach_device(instance['name'], dev_no)
                            self._update_instance_fcp_map_if_unlocked(
                                    instance['name'], fcp_list, self._REMOVE)

    def detach_volume_inactive(self, connection_info, instance, mountpoint,
                               rollback=True):
        """Detach a volume from an shutdown instance."""

        (lun, wwpn, size, fcp) = self._extract_connection_info(None,
                                                               connection_info)
        fcp_list = fcp.split(';')
        self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                  fcp_list, self._DECREASE)
        do_detach = not self._is_fcp_in_use(instance)
        try:
            self._remove_zfcp(instance, fcp, wwpn, lun)
            self._remove_zfcp_from_pool(wwpn, lun)
            self._notice_detach(instance, fcp, wwpn, lun, mountpoint)
            if do_detach:
                for dev_no in fcp_list:
                    self._detach_device(instance['name'], dev_no)
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp_list,
                                                          self._REMOVE)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError,
                exception.ZVMVolumeError):
            with excutils.save_and_reraise_exception():
                self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                          fcp_list,
                                                          self._INCREASE)
                if rollback:
                    with zvmutils.ignore_errors():
                        self._attach_device(instance['name'], fcp)
                    with zvmutils.ignore_errors():
                        self._notice_attach(instance, fcp, wwpn, lun,
                                            mountpoint)
                    with zvmutils.ignore_errors():
                        self._add_zfcp_to_pool(fcp, wwpn, lun, size)
                    with zvmutils.ignore_errors():
                        self._allocate_zfcp(instance, fcp, size, wwpn, lun)

    def volume_boot_init(self, instance, fcp):
        self._update_instance_fcp_map_if_unlocked(instance['name'], [fcp],
                                                  self._INCREASE)
        self._attach_device(instance['name'], fcp)

    def volume_boot_cleanup(self, instance, fcp):
        self._update_instance_fcp_map_if_unlocked(instance['name'],
                                                  [fcp], self._DECREASE)
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

    def _is_fcp_in_use(self, instance):
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
        # Replace the ';' in wwpn/fcp to ',' in script file since shell will
        # treat ';' as a new line
        fcp = fcp.replace(';', ',')
        fcp = "fcpAddr=%s" % fcp
        wwpn = wwpn.replace(';', ',')
        wwpn = "wwpn=%s" % wwpn
        lun = "lun=%s" % lun
        parmline = ' '.join([action, fcp, wwpn, lun])
        return parmline

    def _get_mountpoint_parms(self, action, fcp, wwpn, lun, mountpoint):
        action_parm = "action=%s" % action
        mountpoint = "tgtFile=%s" % mountpoint
        # Replace the ';' in wwpn/fcp to ',' in script file since shell will
        # treat ';' as a new line
        wwpn = wwpn.replace(';', ',')
        fcp = fcp.replace(';', ',')
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
