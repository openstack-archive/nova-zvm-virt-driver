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
import datetime
import operator
import os
import time
import uuid

from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_service import loopingcall
from oslo_utils import excutils
from oslo_utils import timeutils
from oslo_utils import units
from oslo_utils import versionutils

from nova.api.metadata import base as instance_metadata
from nova.compute import power_state
from nova.compute import task_states
from nova.compute import vm_mode
from nova.compute import vm_states
from nova import exception as nova_exception
from nova.i18n import _, _LI, _LW
from nova.image import api as image_api
from nova.image import glance
from nova import utils
from nova.virt import configdrive
from nova.virt import driver
from nova.virt.zvm import configdrive as zvmconfigdrive
from nova.virt.zvm import const
from nova.virt.zvm import dist
from nova.virt.zvm import exception
from nova.virt.zvm import imageop
from nova.virt.zvm import instance as zvminstance
from nova.virt.zvm import networkop
from nova.virt.zvm import utils as zvmutils
from nova.virt.zvm import volumeop
from nova import volume


LOG = logging.getLogger(__name__)

CONF = cfg.CONF
CONF.import_opt('host', 'nova.netconf')
CONF.import_opt('my_ip', 'nova.netconf')
CONF.import_opt('default_ephemeral_format', 'nova.virt.driver')

ZVMInstance = zvminstance.ZVMInstance


class ZVMDriver(driver.ComputeDriver):
    """z/VM implementation of ComputeDriver."""

    capabilities = {
        "has_imagecache": True,
        }

    def __init__(self, virtapi):
        super(ZVMDriver, self).__init__(virtapi)
        self._xcat_url = zvmutils.XCATUrl()

        self._host_stats = []

        # incremental sleep interval list
        _inc_slp = [5, 10, 20, 30, 60]
        _slp = 5
        while (self._host_stats == []):
            try:
                self._host_stats = self.update_host_status()
            except Exception:
                # Ignore any exceptions and log as warning
                _slp = len(_inc_slp) != 0 and _inc_slp.pop(0) or _slp
                LOG.warn(_LW("Failed to get host stats while initializing zVM "
                         "driver, will re-try in %d seconds") % _slp)
                time.sleep(_slp)

        self._networkop = networkop.NetworkOperator()
        self._zvm_images = imageop.ZVMImages()
        self._pathutils = zvmutils.PathUtils()
        self._networkutils = zvmutils.NetworkUtils()
        self._volumeop = volumeop.VolumeOperator()
        self._volume_api = volume.API()
        self._dist_manager = dist.ListDistManager()
        self._image_api = image_api.API()

    def init_host(self, host):
        """Initialize anything that is necessary for the driver to function,
        including catching up with currently running VM's on the given host.
        """
        try:
            self._volumeop.init_host(self._host_stats)
        except Exception as e:
            emsg = zvmutils.format_exception_msg(e)
            LOG.warn(_LW("Exception raised while initializing z/VM driver: %s")
                     % emsg)

    def get_info(self, instance):
        """Get the current status of an instance, by name (not ID!)

        Returns a dict containing:
        :state:           the running state, one of the power_state codes
        :max_mem:         (int) the maximum memory in KBytes allowed
        :mem:             (int) the memory in KBytes used by the domain
        :num_cpu:         (int) the number of virtual CPUs for the domain
        :cpu_time:        (int) the CPU time used in nanoseconds

        """
        inst_name = instance['name']
        zvm_inst = ZVMInstance(instance)

        try:
            return zvm_inst.get_info()
        except exception.ZVMXCATRequestFailed as err:
            emsg = err.format_message()
            if (emsg.__contains__("Invalid nodes and/or groups") and
                    emsg.__contains__("Forbidden")):
                LOG.warn(_LW("z/VM instance %s does not exist") % inst_name,
                         instance=instance)
                raise nova_exception.InstanceNotFound(instance_id=inst_name)
            else:
                raise err

    def list_instances(self):
        """Return the names of all the instances known to the virtualization
        layer, as a list.
        """
        zvm_host = CONF.zvm_host
        hcp_base = self._get_hcp_info()['hostname']

        url = self._xcat_url.tabdump("/zvm")
        res_dict = zvmutils.xcat_request("GET", url)

        instances = []

        with zvmutils.expect_invalid_xcat_resp_data(res_dict):
            data_entries = res_dict['data'][0][1:]
            for data in data_entries:
                l = data.split(",")
                node, hcp = l[0].strip("\""), l[1].strip("\"")
                hcp_short = hcp_base.partition('.')[0]

                # zvm host and zhcp are not included in the list
                if (hcp.upper() == hcp_base.upper() and
                        node.upper() not in (zvm_host.upper(),
                        hcp_short.upper(), CONF.zvm_xcat_master.upper())):
                    instances.append(node)

        return instances

    def instance_exists(self, instance_name):
        """Overwrite this to using instance name as input parameter."""
        return instance_name in self.list_instances()

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None,
              flavor=None):
        """Create a new instance/VM/domain on the virtualization platform.

        Once this successfully completes, the instance should be
        running (power_state.RUNNING).

        If this fails, any partial instance should be completely
        cleaned up, and the virtualization platform should be in the state
        that it was before this call began.

        :param context: security context
        :param instance: Instance object as returned by DB layer.
                         This function should use the data there to guide
                         the creation of the new instance.
        :param image_meta: image object returned by nova.image.glance that
                           defines the image from which to boot this instance
        :param injected_files: User files to inject into instance.
        :param admin_password: Administrator password to set in instance.
        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param block_device_info: Information about block devices to be
                                  attached to the instance.

        """
        # This is because commit fbe31e461ac3f16edb795993558a2314b4c16b52
        # changes the image_meta from dict to object, we have several
        # unique property can't be handled well
        # see bug 1537921 for detail info
        image_meta = self._image_api.get(context, image_meta.id)

        root_mount_device, boot_from_volume = zvmutils.is_boot_from_volume(
                                                            block_device_info)
        bdm = driver.block_device_info_get_mapping(block_device_info)

        if not network_info:
            msg = _("Not support boot without a NIC. "
            "A NIC connected to xCAT management network is required.")
            raise exception.ZVMDriverError(msg=msg)

        # Ensure the used image is a valid zVM image
        if not boot_from_volume:
            self._zvm_images.zimage_check(image_meta)

        compute_node = CONF.zvm_host
        zhcp = self._get_hcp_info()['hostname']

        zvm_inst = ZVMInstance(instance)
        instance_path = self._pathutils.get_instance_path(compute_node,
                                                          zvm_inst._name)
        # Create network configuration files
        LOG.debug('Creating network configuration files '
                    'for instance: %s' % zvm_inst._name, instance=instance)
        base_nic_vdev = CONF.zvm_default_nic_vdev

        if not boot_from_volume:
            os_version = image_meta['properties']['os_version']
        else:
            volume_id = self._extract_volume_id(bdm, root_mount_device)
            volume_summery = self._volume_api.get(context, volume_id)
            volume_meta = volume_summery['volume_metadata']
            os_version = volume_meta['os_version']

        linuxdist = self._dist_manager.get_linux_dist(os_version)()
        files_and_cmds = linuxdist.create_network_configuration_files(
                             instance_path, network_info, base_nic_vdev)
        (net_conf_files, net_conf_cmds) = files_and_cmds
        # Add network configure files to inject_files
        if len(net_conf_files) > 0:
            injected_files.extend(net_conf_files)

        # Create configure drive
        if not CONF.zvm_config_drive_inject_password:
            admin_password = CONF.zvm_image_default_password
        transportfiles = None
        if configdrive.required_by(instance):
            transportfiles = self._create_config_drive(instance_path,
                instance, injected_files, admin_password, net_conf_cmds,
                linuxdist)

        LOG.info(_LI("The instance %(name)s is spawning at %(node)s") %
                 {'name': zvm_inst._name, 'node': compute_node},
                 instance=instance)

        spawn_start = time.time()

        try:
            if not boot_from_volume:
                tmp_file_fn = None
                bundle_file_path = None
                if 'root_disk_units' not in image_meta['properties']:
                    (tmp_file_fn, image_file_path,
                     bundle_file_path) = self._import_image_to_nova(context,
                                                    instance, image_meta)
                    image_meta = self._zvm_images.set_image_root_disk_units(
                                    context, image_meta, image_file_path)
                image_in_xcat = self._zvm_images.image_exist_xcat(
                                    instance['image_ref'])
                if not image_in_xcat:
                    self._import_image_to_xcat(context, instance, image_meta,
                                               tmp_file_fn)
                elif bundle_file_path is not None:
                    self._pathutils.clean_temp_folder(bundle_file_path)

            # Create xCAT node and userid for the instance
            zvm_inst.create_xcat_node(zhcp)
            zvm_inst.create_userid(block_device_info, image_meta)

            # Setup network for z/VM instance
            self._preset_instance_network(zvm_inst._name, network_info)
            nic_vdev = base_nic_vdev
            zhcpnode = self._get_hcp_info()['nodename']
            for vif in network_info:
                LOG.debug('Create nic for instance: %(inst)s, MAC: '
                            '%(mac)s Network: %(network)s Vdev: %(vdev)s' %
                          {'inst': zvm_inst._name, 'mac': vif['address'],
                           'network': vif['network']['label'],
                           'vdev': nic_vdev}, instance=instance)
                self._networkop.create_nic(zhcpnode, zvm_inst._name,
                                           vif['id'],
                                           vif['address'],
                                           nic_vdev)
                nic_vdev = str(hex(int(nic_vdev, 16) + 3))[2:]

            # Call nodeset restapi to deploy image on node
            if not boot_from_volume:
                zvm_inst.update_node_info(image_meta)
                deploy_image_name = self._zvm_images.get_imgname_xcat(
                                        instance['image_ref'])
                zvm_inst.deploy_node(deploy_image_name, transportfiles)
            else:
                zvmutils.punch_configdrive_file(transportfiles, zvm_inst._name)

            # Change vm's admin password during spawn
            zvmutils.punch_adminpass_file(instance_path, zvm_inst._name,
                                          admin_password, linuxdist)

            # Unlock the instance
            zvmutils.punch_xcat_auth_file(instance_path, zvm_inst._name)

            # punch ephemeral disk info to the instance
            if instance['ephemeral_gb'] != 0:
                eph_disks = block_device_info.get('ephemerals', [])
                if eph_disks == []:
                    zvmutils.process_eph_disk(zvm_inst._name)
                else:
                    for idx, eph in enumerate(eph_disks):
                        vdev = zvmutils.generate_eph_vdev(idx)
                        fmt = eph.get('guest_format')
                        mount_dir = ''.join([CONF.zvm_default_ephemeral_mntdir,
                                             str(idx)])
                        zvmutils.process_eph_disk(zvm_inst._name, vdev, fmt,
                                                  mount_dir)

            # Wait until network configuration finish
            self._wait_for_addnic(zvm_inst._name)
            if not self._is_nic_granted(zvm_inst._name):
                msg = _("Failed to bind vswitch")
                LOG.error(msg, instance=instance)
                raise exception.ZVMNetworkError(msg=msg)

            # Attach persistent volume, exclude root volume
            bdm_attach = list(bdm)
            bdm_attach = self._exclude_root_volume_bdm(bdm_attach,
                                                       root_mount_device)
            self._attach_volume_to_instance(context, instance, bdm_attach)

            # 1. Prepare for booting from volume
            # 2. Write the zipl.conf file and issue zipl
            if boot_from_volume:
                (lun, wwpn, size, fcp) = zvm_inst.prepare_volume_boot(context,
                                            instance, bdm, root_mount_device,
                                            volume_meta)
                zvmutils.punch_zipl_file(instance_path, zvm_inst._name,
                                         lun, wwpn, fcp, volume_meta)

            # Power on the instance, then put MN's public key into instance
            zvm_inst.power_on()

            # Update the root device name in instance table
            root_device_name = '/dev/' + const.ZVM_DEFAULT_ROOT_VOLUME
            if boot_from_volume:
                root_device_name = self._format_mountpoint(root_device_name)
            instance.root_device_name = root_device_name
            instance.save()

            spawn_time = time.time() - spawn_start
            LOG.info(_LI("Instance spawned succeeded in %s seconds") %
                     spawn_time, instance=instance)
        except (exception.ZVMXCATCreateNodeFailed,
                exception.ZVMImageError):
            with excutils.save_and_reraise_exception():
                zvm_inst.delete_xcat_node()
        except (exception.ZVMXCATCreateUserIdFailed,
                exception.ZVMNetworkError,
                exception.ZVMVolumeError,
                exception.ZVMXCATUpdateNodeFailed,
                exception.ZVMXCATDeployNodeFailed):
            with excutils.save_and_reraise_exception():
                self.destroy(context, instance, network_info,
                             block_device_info)
        except Exception as err:
            # Just a error log then re-raise
            with excutils.save_and_reraise_exception():
                LOG.error(_("Deploy image to instance %(instance)s "
                            "failed with reason: %(err)s") %
                          {'instance': zvm_inst._name, 'err': err},
                          instance=instance)
        finally:
            self._pathutils.clean_temp_folder(instance_path)

        # Update image last deploy date in xCAT osimage table
        if not boot_from_volume:
            self._zvm_images.update_last_use_date(deploy_image_name)

    def _create_config_drive(self, instance_path, instance, injected_files,
                             admin_password, commands, linuxdist):
        if CONF.config_drive_format not in ['tgz', 'iso9660']:
            msg = (_("Invalid config drive format %s") %
                   CONF.config_drive_format)
            raise exception.ZVMConfigDriveError(msg=msg)

        LOG.debug('Using config drive', instance=instance)

        extra_md = {}
        if CONF.zvm_config_drive_inject_password:
            extra_md['admin_pass'] = admin_password

        udev_settle = linuxdist.get_znetconfig_contents()
        if len(commands) == 0:
            znetconfig = '\n'.join(('# !/bin/sh', udev_settle))
        else:
            znetconfig = '\n'.join(('# !/bin/sh', commands, udev_settle))
        znetconfig += '\nrm -rf /tmp/znetconfig.sh\n'
        # Create a temp file in instance to execute above commands
        net_cmd_file = []
        net_cmd_file.append(('/tmp/znetconfig.sh', znetconfig))
        injected_files.extend(net_cmd_file)
        # injected_files.extend(('/tmp/znetconfig.sh', znetconfig))

        inst_md = instance_metadata.InstanceMetadata(instance,
                                                 content=injected_files,
                                                 extra_md=extra_md)

        configdrive_tgz = os.path.join(instance_path, 'cfgdrive.tgz')

        LOG.debug('Creating config drive at %s' % configdrive_tgz,
                  instance=instance)
        with zvmconfigdrive.ZVMConfigDriveBuilder(instance_md=inst_md) as cdb:
            cdb.make_drive(configdrive_tgz)

        return configdrive_tgz

    def _preset_instance_network(self, instance_name, network_info):
        self._networkop.config_xcat_mac(instance_name)
        LOG.debug("Add ip/host name on xCAT MN for instance %s" %
                    instance_name)
        try:
            network = network_info[0]['network']
            ip_addr = network['subnets'][0]['ips'][0]['address']
        except Exception:
            if network_info:
                msg = _("Invalid network info: %s") % str(network_info)
            else:
                msg = _("Network info is Empty")
            raise exception.ZVMNetworkError(msg=msg)

        self._networkop.add_xcat_host(instance_name, ip_addr, instance_name)
        self._networkop.makehosts()

    def _import_image_to_nova(self, context, instance, image_meta):
        image_file_name = image_meta['properties']['image_file_name']
        disk_file = ''.join(j for j in image_file_name.split(".img")[0]
                            if j.isalnum()) + ".img"
        tmp_file_fn = self._pathutils.make_time_stamp()
        bundle_file_path = self._pathutils.get_bundle_tmp_path(tmp_file_fn)
        image_file_path = self._pathutils.get_img_path(
                                            bundle_file_path, disk_file)

        LOG.debug("Downloading the image %s from glance to nova compute "
                    "server" % image_meta['id'], instance=instance)
        self._zvm_images.fetch_image(context,
                                     image_meta['id'],
                                     image_file_path,
                                     instance['user_id'],
                                     instance['project_id'])
        return (tmp_file_fn, image_file_path, bundle_file_path)

    def _import_image_to_xcat(self, context, instance, image_meta, tmp_f_fn):
        # Format the image name and image disk file in case user named them
        # with special characters
        image_name = ''.join(i for i in image_meta['name'] if i.isalnum())
        spawn_path = self._pathutils.get_spawn_folder()
        image_file_name = image_meta['properties']['image_file_name']
        disk_file = ''.join(j for j in image_file_name.split(".img")[0]
                           if j.isalnum()) + ".img"
        if tmp_f_fn is None:
            tmp_f_fn = self._pathutils.make_time_stamp()
            bundle_file_path = self._pathutils.get_bundle_tmp_path(tmp_f_fn)
            image_file_path = self._pathutils.get_img_path(
                bundle_file_path, disk_file)
            LOG.debug("Downloading the image %s from glance to nova compute "
                    "server" % image_meta['id'], instance=instance)
            self._zvm_images.fetch_image(context,
                                     image_meta['id'],
                                     image_file_path,
                                     instance['user_id'],
                                     instance['project_id'])
        else:
            bundle_file_path = self._pathutils.get_bundle_tmp_path(tmp_f_fn)
            image_file_path = self._pathutils.get_img_path(
                bundle_file_path, disk_file)

        LOG.debug("Generating the manifest.xml as a part of bundle file for "
                    "image %s" % image_meta['id'], instance=instance)
        self._zvm_images.generate_manifest_file(image_meta, image_name,
                                                 disk_file, bundle_file_path)

        LOG.debug("Generating bundle file for image %s" % image_meta['id'],
                  instance=instance)
        image_bundle_package = self._zvm_images.generate_image_bundle(
                                    spawn_path, tmp_f_fn, image_name)

        LOG.debug("Importing the image %s to xCAT" % image_meta['id'],
                  instance=instance)
        profile_str = image_name, instance['image_ref'].replace('-', '_')
        image_profile = '_'.join(profile_str)
        self._zvm_images.check_space_imgimport_xcat(context, instance,
            image_bundle_package, CONF.xcat_free_space_threshold,
            CONF.zvm_xcat_master)
        self._zvm_images.put_image_to_xcat(image_bundle_package,
                                           image_profile)

    @property
    def need_legacy_block_device_info(self):
        return False

    def destroy(self, context, instance, network_info=None,
                block_device_info=None, destroy_disks=False):
        """Destroy (shutdown and delete) the specified instance.

        If the instance is not found (for example if networking failed), this
        function should still succeed.  It's probably a good idea to log a
        warning in that case.

        :param instance: Instance object as returned by DB layer.
        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param block_device_info: Information about block devices that should
                                  be detached from the instance.
        :param destroy_disks: Indicates if disks should be destroyed

        """
        inst_name = instance['name']
        root_mount_device, boot_from_volume = zvmutils.is_boot_from_volume(
                                                            block_device_info)
        zvm_inst = ZVMInstance(instance)

        if self.instance_exists(inst_name):
            LOG.info(_LI("Destroying instance %s") % inst_name,
                     instance=instance)

            bdm = driver.block_device_info_get_mapping(block_device_info)
            try:
                bdm_det = list(bdm)
                bdm_det = self._exclude_root_volume_bdm(bdm_det,
                                                        root_mount_device)
                self._detach_volume_from_instance(instance, bdm_det)
                if boot_from_volume:
                    zvm_inst.clean_volume_boot(context, instance, bdm,
                                               root_mount_device)
            except exception.ZVMBaseException as err:
                LOG.warn(_LW("Failed to detach volume: %s") %
                         err.format_message(), instance=instance)

            if network_info:
                try:
                    for vif in network_info:
                        self._networkop.clean_mac_switch_host(inst_name)
                except exception.ZVMNetworkError:
                    LOG.warn(_LW("Clean MAC and VSWITCH failed while "
                                 "destroying z/VM instance %s") % inst_name,
                             instance=instance)

            zvm_inst.delete_userid(self._get_hcp_info()['nodename'])
        else:
            LOG.warn(_LW('Instance %s does not exist') % inst_name,
                     instance=instance)

    def manage_image_cache(self, context, filtered_instances):
        """Clean the image cache in xCAT MN."""
        LOG.info(_LI("Check and clean image cache in xCAT"))
        clean_period = CONF.xcat_image_clean_period
        self._zvm_images.clean_image_cache_xcat(clean_period)

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        """Reboot the specified instance.

        After this is called successfully, the instance's state
        goes back to power_state.RUNNING. The virtualization
        platform should ensure that the reboot action has completed
        successfully even in cases in which the underlying domain/vm
        is paused or halted/stopped.

        :param instance: Instance object as returned by DB layer.
        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param reboot_type: Either a HARD or SOFT reboot
        :param block_device_info: Info pertaining to attached volumes
        :param bad_volumes_callback: Function to handle any bad volumes
            encountered

        """
        zvm_inst = ZVMInstance(instance)
        if reboot_type == 'SOFT':
            zvm_inst.reboot()
        else:
            zvm_inst.reset()

        if not zvm_inst._reachable:
            LOG.error(_("Failed to reboot instance %s: timeout") %
                      zvm_inst._name, instance=instance)
            raise nova_exception.InstanceRebootFailure(reason=_("timeout"))

    def get_host_ip_addr(self):
        """Retrieves the IP address of the dom0."""
        return CONF.my_ip

    def _format_mountpoint(self, mountpoint):
        """Change mountpoint from /dev/sdX to /dev/vdX.

        When a SCSI device is pluged in, the system will create a file node
        /dev/sdX for the SCSI device. If the file node exists already as a
        link to another file, the link will be overlayed and the file node
        will be seized by the SCSI device.

        For example, if the first SCSI device is pluged in and the mountpoint
        is specified as /dev/sdb, the SCSI device will be attached to /dev/sda
        and /dev/sdb is created as a link to /dev/sda. Then the second SCSI
        device is pluged in. It will be attached to /dev/sdb and the link will
        no longer exist.

        To avoid this case, if mountpoint is /dev/sdX, it will be changed to
        /dev/vdX. Otherwize it will keep as it is.

        When instance's root_device_name is /dev/dasdX, the mountpoint will be
        changed to /dev/dX. That's not what is expected. Format mountpoint to
        /dev/vdX in this case.

        :param mountpoint: The file node name of the mountpoint.

        """

        mountpoint = mountpoint.lower()
        mountpoint = mountpoint.replace('/dev/d', '/dev/sd')
        return mountpoint.replace('/dev/s', '/dev/v')

    def attach_volume(self, context, connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None):
        """Attach the disk to the instance at mountpoint using info."""
        if instance.vm_state == vm_states.PAUSED:
            msg = _("Attaching to a paused instance is not supported.")
            raise exception.ZVMDriverError(msg=msg)
        if mountpoint:
            mountpoint = self._format_mountpoint(mountpoint)
        if self.instance_exists(instance['name']):
            zvm_inst = ZVMInstance(instance)
            is_active = zvm_inst.is_reachable()

            zvm_inst.attach_volume(self._volumeop, context, connection_info,
                                   instance, mountpoint, is_active)

    def detach_volume(self, connection_info, instance, mountpoint=None,
                      encryption=None):
        """Detach the disk attached to the instance."""
        if instance.vm_state == vm_states.PAUSED:
            msg = _("Detaching from a paused instance is not supported.")
            raise exception.ZVMDriverError(msg=msg)
        if mountpoint:
            mountpoint = self._format_mountpoint(mountpoint)
        if self.instance_exists(instance['name']):
            zvm_inst = ZVMInstance(instance)
            is_active = zvm_inst.is_reachable()

            zvm_inst.detach_volume(self._volumeop, connection_info, instance,
                                   mountpoint, is_active)

    def _reset_power_state(self, state, instance):
        # If the instance's power_state is "RUNNING", power it on after
        # capture. If "PAUSED", pause it after capture.
        if state == power_state.RUNNING or state == power_state.PAUSED:
            try:
                self.power_on({}, instance, [])
            except nova_exception.InstancePowerOnFailure as err:
                LOG.warn(_LW("Power On instance %(inst)s fail after capture, "
                    "please check manually. The error is: %(err)s") %
                    {'inst': instance['name'], 'err': err.format_message()},
                    instance=instance)
        if state == power_state.PAUSED:
            try:
                self.pause(instance)
            except (exception.ZVMXCATRequestFailed,
                    exception.ZVMInvalidXCATResponseDataError,
                    exception.ZVMXCATInternalError) as err:
                LOG.warn(_LW("Pause instance %(inst)s fail after capture, "
                    "please check manually. The error is: %(err)s") %
                    {'inst': instance['name'], 'err': err.format_message()},
                    instance=instance)

    def _get_xcat_image_file_path(self, image_name_xcat):
        """Get image file path from image name in xCAT."""
        image_path = self._zvm_images.get_image_file_path_from_image_name(
                                                            image_name_xcat)
        image_name = self._zvm_images.get_image_file_name(image_path)
        return '/'.join((image_path, image_name))

    def _is_shared_image_repo(self, image_name_xcat):
        """To check whether nova can access xCAT image repo."""
        try:
            image_file_path = self._get_xcat_image_file_path(image_name_xcat)
        except exception.ZVMImageError:
            # image path not exist or image file not found
            return False

        return os.stat(image_file_path).st_mode & 4 == 4

    def snapshot(self, context, instance, image_href, update_task_state):
        """Snapshots the specified instance.

        :param context: security context
        :param instance: Instance object as returned by DB layer.
        :param image_href: Reference to a pre-created image that will
                         hold the snapshot.
        """
        # Check the image status
        (image_service, image_id) = glance.get_remote_image_service(context,
                                                                    image_href)
        image_meta = image_service.show(context, image_href)

        # remove user names special characters, this name will only be used
        # to pass to xcat and combine with UUID in xcat.
        image_name = ''.join(i for i in image_meta['name'] if i.isalnum())
        image_name_xcat = None

        # Make sure the instance's power_state is running and unpaused before
        # doing a capture.
        state = instance['power_state']
        if (state == power_state.NOSTATE or state == power_state.CRASHED or
                state == power_state.SUSPENDED):
            raise nova_exception.InstanceNotReady(instance_id=instance['name'])
        elif state == power_state.SHUTDOWN:
            self.power_on({}, instance, [])
        elif state == power_state.PAUSED:
            self.unpause(instance)

        # Check xCAT free space and invoke the zvmimages.create_zvm_image()
        try:
            free_space_xcat = self._zvm_images.get_free_space_xcat(
                                  CONF.xcat_free_space_threshold,
                                  CONF.zvm_xcat_master)
            imgcapture_needed = self._zvm_images.get_imgcapture_needed(
                                    instance)
            if (free_space_xcat - imgcapture_needed) < 0:
                larger = max(CONF.xcat_free_space_threshold, imgcapture_needed)
                size_needed = float(larger - free_space_xcat)
                self._zvm_images.prune_image_xcat(context, size_needed,
                                              imgcapture_needed)
            image_name_xcat = self._zvm_images.create_zvm_image(instance,
                                                                image_name,
                                                                image_href)
            # Update image last create date in xCAT osimage table
            self._zvm_images.update_last_use_date(image_name_xcat)
        except (exception.ZVMImageError,
                exception.ZVMXCATXdshFailed):
            with excutils.save_and_reraise_exception():
                self._reset_power_state(state, instance)
                self._zvm_images.delete_image_glance(image_service, context,
                                                     image_href)

        self._reset_power_state(state, instance)
        shared_image_repo = self._is_shared_image_repo(image_name_xcat)

        if not shared_image_repo:
            # The image will be exported from xCAT and imported to nova after
            # successfully captured.
            snapshot_time_path = self._zvm_images.get_snapshot_time_path()
            try:
                image_bundle = self._zvm_images.get_image_from_xcat(
                               image_name_xcat, image_name, snapshot_time_path)
            except exception.ZVMImageError:
                with excutils.save_and_reraise_exception():
                    self._zvm_images.delete_image_glance(image_service,
                                                         context, image_href)
                    self._zvm_images.clean_up_snapshot_time_path(
                                                          snapshot_time_path)
                    self._zvm_images.delete_image_from_xcat(image_name_xcat)

            # The image in the xCAT MN will be removed after imported to nova
            # Invoke rmimage REST API twice to remove image and object
            self._zvm_images.delete_image_from_xcat(image_name_xcat)

            # Untar the image_bundle and parse manifest.xml
            image_package_path = os.path.join(snapshot_time_path,
                                              image_name_xcat)
            try:
                self._zvm_images.untar_image_bundle(snapshot_time_path,
                                                    image_bundle)
                manifest = self._zvm_images.parse_manifest_xml(
                                                            image_package_path)
                image_file_name = self._zvm_images.get_image_file_name(
                                      image_package_path)
                image_file_path = '/'.join((image_package_path,
                                            image_file_name))
            except exception.ZVMImageError:
                with excutils.save_and_reraise_exception():
                    self._zvm_images.delete_image_glance(image_service,
                                                         context, image_href)
                    self._zvm_images.clean_up_snapshot_time_path(
                                                        snapshot_time_path)
        else:
            image_file_path = self._get_xcat_image_file_path(image_name_xcat)
            (image_package_path, _toss,
                image_file_name) = image_file_path.rpartition('/')
            manifest = self._zvm_images.get_image_menifest(image_name_xcat)

        root_disk_units = self._zvm_images.get_root_disk_units(image_file_path)

        # Before upload, update the instance task_state to image_pending_upload
        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)

        # manifest.xml contributes some new image meta
        LOG.debug("Snapshot extracted, beginning image upload",
                  instance=instance)
        new_image_meta = {
            'is_public': False,
            'status': 'active',
            'properties': {
                 'image_location': 'snapshot',
                 'image_state': 'available',
                 'owner_id': instance['project_id'],
                 'image_type_xcat': manifest['imagetype'],
                 'type': 'snapshot',
                 'architecture': manifest['osarch'],
                 'os_name': manifest['osname'],
                 'os_version': manifest['osvers'],
                 'image_profile': manifest['profile'],
                 'provisioning_method': manifest['provmethod'],
                 'image_file_name': image_file_name,
                 'hypervisor_type': const.HYPERVISOR_TYPE,
                 'root_disk_units': root_disk_units
            },
            'disk_format': 'raw',
            'container_format': 'bare',
        }

        # Upload that image to the image service
        image_path = os.path.join(image_package_path, image_file_name)
        update_task_state(task_state=task_states.IMAGE_UPLOADING,
                          expected_state=task_states.IMAGE_PENDING_UPLOAD)

        def cleanup_temp_image():
            if not shared_image_repo:
                # Clean up temp image in nova snapshot temp folder
                self._zvm_images.clean_up_snapshot_time_path(
                    snapshot_time_path)
            else:
                # Clean up image from xCAT image repo for all-in-one mode
                self._zvm_images.delete_image_from_xcat(image_name_xcat)

        try:
            with open(image_path, 'r') as image_file:
                image_service.update(context,
                                     image_href,
                                     new_image_meta,
                                     image_file)
        except Exception:
            with excutils.save_and_reraise_exception():
                self._zvm_images.delete_image_glance(image_service, context,
                                                     image_href)
                cleanup_temp_image()

        LOG.debug("Snapshot image upload complete", instance=instance)

        cleanup_temp_image()

        LOG.info(_LI("Snapshot complete successfully"), instance=instance)

    def pause(self, instance):
        """Pause the specified instance."""
        LOG.debug('Pausing %s' % instance['name'], instance=instance)
        zvm_inst = ZVMInstance(instance)
        zvm_inst.pause()

    def unpause(self, instance):
        """Unpause paused VM instance."""
        LOG.debug('Un-pausing %s' % instance['name'], instance=instance)
        zvm_inst = ZVMInstance(instance)
        zvm_inst.unpause()

    def power_off(self, instance, timeout=0, retry_interval=0):
        """Power off the specified instance."""
        LOG.debug('Stopping z/VM instance %s' % instance['name'],
                  instance=instance)
        zvm_inst = ZVMInstance(instance)
        zvm_inst.power_off()

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        """Power on the specified instance."""
        LOG.debug('Starting z/VM instance %s' % instance['name'],
                  instance=instance)
        zvm_inst = ZVMInstance(instance)
        zvm_inst.power_on()

    def get_available_resource(self, nodename=None):
        """Retrieve resource information.

        This method is called when nova-compute launches, and
        as part of a periodic task

        :param nodename:
            node which the caller want to get resources from
            a driver that manages only one node can safely ignore this
        :returns: Dictionary describing resources

        """
        LOG.debug("Getting available resource for %s" % CONF.zvm_host)
        stats = self.update_host_status()[0]

        mem_used = stats['host_memory_total'] - stats['host_memory_free']
        supported_instances = stats['supported_instances']
        dic = {
            'vcpus': stats['vcpus'],
            'memory_mb': stats['host_memory_total'],
            'local_gb': stats['disk_total'],
            'vcpus_used': stats['vcpus_used'],
            'memory_mb_used': mem_used,
            'local_gb_used': stats['disk_used'],
            'hypervisor_type': stats['hypervisor_type'],
            'hypervisor_version': stats['hypervisor_version'],
            'hypervisor_hostname': stats['hypervisor_hostname'],
            'cpu_info': jsonutils.dumps(stats['cpu_info']),
            'disk_available_least': stats['disk_available'],
            'supported_instances': supported_instances,
            'numa_topology': None,
        }

        return dic

    def check_can_live_migrate_destination(self, ctxt, instance_ref,
                                           src_compute_info, dst_compute_info,
                                           block_migration=False,
                                           disk_over_commit=False):
        """Check if it is possible to execute live migration.

        This runs checks on the destination host, and then calls
        back to the source host to check the results.

        :param ctxt: security context
        :param instance_ref: nova.db.sqlalchemy.models.Instance
        :param src_compute_info: Info about the sending machine
        :param dst_compute_info: Info about the receiving machine
        :param block_migration: if true, prepare for block migration
        :param disk_over_commit: if true, allow disk over commit
        """
        # For z/VM, all live migration check will be done in
        # check_can_live_migration_source, so just return a dst_compute_info.
        # And we only support shared storage live migration.
        migrate_data = {'dest_host': dst_compute_info['hypervisor_hostname'],
                        'is_shared_storage': True}
        dest_check_data = {'migrate_data': migrate_data}

        return dest_check_data

    def check_can_live_migrate_source(self, ctxt, instance_ref,
                                      dest_check_data, block_device_info=None):
        """Check if it is possible to execute live migration.

        This checks if the live migration can succeed, based on the
        results from check_can_live_migrate_destination.

        :param context: security context
        :param instance_ref: nova.db.sqlalchemy.models.Instance
        :param dest_check_data: result of check_can_live_migrate_destination

        """
        LOG.info(_LI("Checking source host for live-migration for %s") %
                 instance_ref['name'], instance=instance_ref)

        migrate_data = dest_check_data.get('migrate_data', {})
        dest_host = migrate_data.get('dest_host', None)
        userid = zvmutils.get_userid(instance_ref['name'])
        migrate_data.update({'source_xcat_mn': CONF.zvm_xcat_server,
                             'zvm_userid': userid})

        if dest_host is not None:
            try:
                self._vmrelocate(dest_host, instance_ref['name'], 'test')
            except nova_exception.MigrationError as err:
                emsg = err.format_message()
                if isinstance(CONF.zvm_vmrelocate_force, str):
                    force = CONF.zvm_vmrelocate_force.lower()
                    if ('domain' in force) or ('architecture' in force):
                        if '1944' in emsg:
                            # force domain/architecture in effect, ignore
                            return migrate_data
                LOG.error(_("Live-migrating check failed: %s") % emsg,
                          instance=instance_ref)
                raise nova_exception.MigrationPreCheckError(reason=emsg)

            return migrate_data
        else:
            reason = _("Invalid migration data")
            raise nova_exception.MigrationPreCheckError(reason=reason)

    def check_can_live_migrate_destination_cleanup(self, ctxt,
                                                   dest_check_data):
        """Do required cleanup on dest host after check_can_live_migrate calls

        :param ctxt: security context
        :param dest_check_data: result of check_can_live_migrate_destination

        """
        # For z/VM, nothing needed to be cleanup
        return

    def pre_live_migration(self, ctxt, instance_ref, block_device_info,
                           network_info, disk_info, migrate_data=None):
        """Prepare an instance for live migration

        :param ctxt: security context
        :param instance_ref: instance object that will be migrated
        :param block_device_info: instance block device information
        :param network_info: instance network information
        :param migrate_data: implementation specific data dict.
        """
        if block_device_info in ([], None, {}):
            msg = _("Not supported live-migration with persistent "
                    "volume attached")
            LOG.error(msg, instance=instance_ref)
            raise nova_exception.MigrationError(reason=msg)

        zvm_inst = ZVMInstance(instance_ref)
        source_xcat_mn = migrate_data.get('source_xcat_mn', '')
        userid = migrate_data.get('zvm_userid')
        hcp = self._get_hcp_info()['hostname']
        same_xcat_mn = source_xcat_mn == CONF.zvm_xcat_server
        dest_diff_mn_key = None

        if not same_xcat_mn:
            # The two z/VM system managed by two different xCAT MN
            zvm_inst.create_xcat_node(hcp, userid)
            dest_diff_mn_key = zvmutils.get_mn_pub_key()
        if network_info is not None:
            network = network_info[0]['network']
            ip_addr = network['subnets'][0]['ips'][0]['address']
            self._networkop.add_xcat_host(zvm_inst._name, ip_addr,
                                          zvm_inst._name)
            self._networkop.makehosts()

        return {'same_xcat_mn': same_xcat_mn,
                'dest_diff_mn_key': dest_diff_mn_key}

    def pre_block_migration(self, ctxt, instance_ref, disk_info):
        """Prepare a block device for migration

        :param ctxt: security context
        :param instance_ref: instance object that will have its disk migrated
        :param disk_info: information about disk to be migrated (as returned
                          from get_instance_disk_info())
        """
        # We don't support block_migration
        return

    def live_migration(self, ctxt, instance_ref, dest,
                       post_method, recover_method, block_migration=False,
                       migrate_data=None):
        """Live migration of an instance to another host.

        :params ctxt: security context
        :params instance_ref:
            nova.db.sqlalchemy.models.Instance object
            instance object that is migrated.
        :params dest: destination host
        :params post_method:
            post operation method.
            expected nova.compute.manager.post_live_migration.
        :params recover_method:
            recovery method when any exception occurs.
            expected nova.compute.manager.recover_live_migration.
        :params block_migration: if true, migrate VM disk.
        :params migrate_data: implementation specific params.

        """
        inst_name = instance_ref['name']
        dest_host = migrate_data['dest_host']
        LOG.info(_LI("Live-migrating %(inst)s to %(dest)s") %
                 {'inst': inst_name, 'dest': dest_host}, instance=instance_ref)

        same_mn = migrate_data['pre_live_migration_result']['same_xcat_mn']
        dest_diff_mn_key = migrate_data['pre_live_migration_result'].get(
                            'dest_diff_mn_key', None)

        if not same_mn and dest_diff_mn_key:
            auth_command = ('echo "%s" >> /root/.ssh/authorized_keys' %
                dest_diff_mn_key)
            zvmutils.xdsh(inst_name, auth_command)

        try:
            self._vmrelocate(dest_host, inst_name, 'move')
        except nova_exception.MigrationError as err:
            LOG.error(_("Live-migration failed: %s") % err.format_message(),
                      instance=instance_ref)
            with excutils.save_and_reraise_exception():
                recover_method(ctxt, instance_ref, dest,
                               block_migration, migrate_data)

        if not same_mn:
            # Delete node definition at source xCAT MN
            zvm_inst = ZVMInstance(instance_ref)
            self._networkop.clean_mac_switch_host(zvm_inst._name)
            zvm_inst.delete_xcat_node()

        post_method(ctxt, instance_ref, dest,
                    block_migration, migrate_data)

    def post_live_migration_at_destination(self, ctxt, instance_ref,
                                           network_info,
                                           block_migration=False,
                                           block_device_info=None):
        """Post operation of live migration at destination host.

        :param ctxt: security context
        :param instance_ref: instance object that is migrated
        :param network_info: instance network information
        :param block_migration: if true, post operation of block_migration.

        """
        inst_name = instance_ref['name']
        nic_vdev = CONF.zvm_default_nic_vdev
        zhcp = self._get_hcp_info()['hostname']

        for vif in network_info:
            LOG.debug('Create nic for instance: %(inst)s, MAC: '
                        '%(mac)s Network: %(network)s Vdev: %(vdev)s' %
                      {'inst': inst_name, 'mac': vif['address'],
                       'network': vif['network']['label'], 'vdev': nic_vdev},
                      instance=instance_ref)
            self._networkop.add_xcat_mac(inst_name, nic_vdev,
                                         vif['address'], zhcp)
            self._networkop.add_xcat_switch(inst_name, vif['id'],
                                            nic_vdev, zhcp)
            nic_vdev = str(hex(int(nic_vdev, 16) + 3))[2:]

    def unfilter_instance(self, instance, network_info):
        """Stop filtering instance."""
        # Not supported for now
        return

    def _vmrelocate(self, dest_host, instance_name, action):
        """Perform live-migration."""
        body = ['destination=%s' % dest_host,
                'action=%s' % action,
                'immediate=%s' % CONF.zvm_vmrelocate_immediate,
                'max_total=%s' % CONF.zvm_vmrelocate_max_total,
                'max_quiesce=%s' % CONF.zvm_vmrelocate_max_quiesce]
        if CONF.zvm_vmrelocate_force is not None:
            body.append('force=%s' % CONF.zvm_vmrelocate_force)

        url = self._xcat_url.rmigrate('/' + instance_name)
        try:
            res = zvmutils.xcat_request("PUT", url, body)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError) as err:
            raise nova_exception.MigrationError(reason=err.format_message())

        res_info = res['info']
        if not (len(res_info) > 0 and
                len(res_info[0]) > 0 and
                res_info[-1][0].__contains__("Done")):
            msg = _("Live-migration failed: %s") % str(res_info)
            LOG.error(msg)
            raise nova_exception.MigrationError(reason=msg)

    def reset_network(self, instance):
        """reset networking for specified instance."""
        # TODO(rui): to implement this later.
        pass

    def inject_network_info(self, instance, nw_info):
        """inject network info for specified instance."""
        # TODO(rui): to implement this later.
        pass

    def plug_vifs(self, instance, network_info):
        """Plug VIFs into networks."""
        # TODO(rui): to implement this later.
        pass

    def unplug_vifs(self, instance, network_info):
        """Unplug VIFs from networks."""
        # TODO(rui): to implement this later
        pass

    def ensure_filtering_rules_for_instance(self, instance_ref, network_info):
        # It enforces security groups on host initialization and live
        # migration. In z/VM we do not assume instances running upon host
        # initialization
        return

    def update_host_status(self):
        """Refresh host stats. One compute service entry possibly
        manages several hypervisors, so will return a list of host
        status information.
        """
        LOG.debug("Updating host status for %s" % CONF.zvm_host)

        caps = []
        host = CONF.zvm_host

        info = self._get_host_inventory_info(host)

        data = {'host': CONF.host,
                'allowed_vm_type': const.ALLOWED_VM_TYPE}
        data['vcpus'] = info['vcpus']
        data['vcpus_used'] = info['vcpus_used']
        data['cpu_info'] = info['cpu_info']
        data['disk_total'] = info['disk_total']
        data['disk_used'] = info['disk_used']
        data['disk_available'] = info['disk_available']
        data['host_memory_total'] = info['memory_mb']
        data['host_memory_free'] = (info['memory_mb'] -
                                    info['memory_mb_used'])
        data['hypervisor_type'] = info['hypervisor_type']
        data['hypervisor_version'] = info['hypervisor_version']
        data['hypervisor_hostname'] = info['hypervisor_hostname']
        data['supported_instances'] = [(const.ARCHITECTURE,
                                        const.HYPERVISOR_TYPE,
                                        vm_mode.HVM)]
        data['zhcp'] = self._get_hcp_info(info['zhcp'])
        data['ipl_time'] = info['ipl_time']

        caps.append(data)

        return caps

    def _get_hcp_info(self, hcp_hostname=None):
        if self._host_stats != []:
            return self._host_stats[0]['zhcp']
        else:
            if hcp_hostname is not None:
                hcp_node = hcp_hostname.partition('.')[0]
                return {'hostname': hcp_hostname,
                        'nodename': hcp_node,
                        'userid': zvmutils.get_userid(hcp_node)}
            else:
                self._host_stats = self.update_host_status()
                return self._host_stats[0]['zhcp']

    def get_volume_connector(self, instance):
        """Get connector information for the instance for attaching to volumes.

        Connector information is a dictionary representing the ip of the
        machine that will be making the connection, the name of the iscsi
        initiator and the hostname of the machine as follows::

            {
                'zvm_fcp': fcp
                'wwpns': [wwpn]
                'host': host
            }
        """
        LOG.debug("Getting volume connector")

        res = self._volumeop.get_volume_connector(instance)

        return res

    def _get_host_inventory_info(self, host):
        url = self._xcat_url.rinv('/' + host)
        inv_info_raw = zvmutils.xcat_request("GET", url)['info'][0]
        inv_keys = const.XCAT_RINV_HOST_KEYWORDS
        inv_info = zvmutils.translate_xcat_resp(inv_info_raw[0], inv_keys)
        dp_info = self._get_diskpool_info(host)

        host_info = {}

        with zvmutils.expect_invalid_xcat_resp_data(inv_info):
            host_info['vcpus'] = int(inv_info['lpar_cpu_total'])
            host_info['vcpus_used'] = int(inv_info['lpar_cpu_used'])
            host_info['cpu_info'] = {}
            host_info['cpu_info'] = {'architecture': const.ARCHITECTURE,
                                     'cec_model': inv_info['cec_model'], }
            host_info['disk_total'] = dp_info['disk_total']
            host_info['disk_used'] = dp_info['disk_used']
            host_info['disk_available'] = dp_info['disk_available']
            mem_mb = zvmutils.convert_to_mb(inv_info['lpar_memory_total'])
            host_info['memory_mb'] = mem_mb
            mem_mb_used = zvmutils.convert_to_mb(inv_info['lpar_memory_used'])
            host_info['memory_mb_used'] = mem_mb_used
            host_info['hypervisor_type'] = const.HYPERVISOR_TYPE
            verl = inv_info['hypervisor_os'].split()[1].split('.')
            version = int(''.join(verl))
            host_info['hypervisor_version'] = version
            host_info['hypervisor_hostname'] = inv_info['hypervisor_name']
            host_info['zhcp'] = inv_info['zhcp']
            host_info['ipl_time'] = inv_info['ipl_time']

        return host_info

    def _get_diskpool_info(self, host):
        addp = '&field=--diskpoolspace&field=' + CONF.zvm_diskpool
        url = self._xcat_url.rinv('/' + host, addp)
        res_dict = zvmutils.xcat_request("GET", url)

        dp_info_raw = res_dict['info'][0]
        dp_keys = const.XCAT_DISKPOOL_KEYWORDS
        dp_info = zvmutils.translate_xcat_resp(dp_info_raw[0], dp_keys)

        with zvmutils.expect_invalid_xcat_resp_data(dp_info):
            for k in list(dp_info.keys()):
                s = dp_info[k].strip().upper()
                if s.endswith('G'):
                    sl = s[:-1].split('.')
                    n1, n2 = int(sl[0]), int(sl[1])
                    if n2 >= 5:
                        n1 += 1
                    dp_info[k] = n1
                elif s.endswith('M'):
                    n_mb = int(s[:-1])
                    n_gb, n_ad = n_mb / 1024, n_mb % 1024
                    if n_ad >= 512:
                        n_gb += 1
                    dp_info[k] = n_gb
                else:
                    exp = "ending with a 'G' or 'M'"
                    errmsg = _("Invalid diskpool size format: %(invalid)s; "
                        "Expected: %(exp)s") % {'invalid': s, 'exp': exp}
                    LOG.error(errmsg)
                    raise exception.ZVMDriverError(msg=errmsg)

        return dp_info

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   instance_type, network_info,
                                   block_device_info=None,
                                   timeout=0, retry_interval=0):
        """Transfers the disk of a running instance in multiple phases, turning
        off the instance before the end.
        """
        is_volume_base = zvmutils.is_boot_from_volume(block_device_info)[1]

        if is_volume_base:
            msg = _("Not support boot from volume.")
            LOG.error(msg, instance=instance)
            raise nova_exception.InstanceFaultRollback(
                nova_exception.MigrationError(reason=msg))

        new_root_disk_size = instance_type.root_gb
        new_eph_disk_size = instance_type.ephemeral_gb
        old_root_disk_size = instance.root_gb
        old_eph_disk_size = instance.ephemeral_gb

        if (new_root_disk_size < old_root_disk_size or
                new_eph_disk_size < old_eph_disk_size):
            err = _("Not support shrink disk")
            LOG.error(err, instance=instance)
            raise nova_exception.InstanceFaultRollback(
                nova_exception.MigrationError(reason=err))

        # Make sure the instance's power_state is running and unpaused before
        # doing a capture.
        state = instance['power_state']
        if (state == power_state.NOSTATE or state == power_state.CRASHED or
                state == power_state.SUSPENDED):
            raise nova_exception.InstanceNotReady(instance_id=instance['name'])
        elif state == power_state.SHUTDOWN:
            self.power_on({}, instance, [])
        elif state == power_state.PAUSED:
            self.unpause(instance)

        inst_name = instance['name']
        LOG.debug("Starting to migrate instance %s" % inst_name,
                  instance=instance)

        disk_owner = zvmutils.get_userid(inst_name)
        eph_disk_info = self._get_eph_disk_info(inst_name)

        bdm = driver.block_device_info_get_mapping(block_device_info)
        self._detach_volume_from_instance(instance, bdm)

        image_name_xcat = self._capture_disk_for_instance(context, instance)

        image_bundle = ''
        shared_image_repo = self._is_shared_image_repo(image_name_xcat)
        if not shared_image_repo:
            # Export image from xCAT to compute node if image repo not shared
            snapshot_time_path = self._zvm_images.get_snapshot_time_path()

            try:
                image_bundle = self._zvm_images.get_image_from_xcat(
                                                         image_name_xcat,
                                                         image_name_xcat,
                                                         snapshot_time_path)
            except exception.ZVMImageError:
                with excutils.save_and_reraise_exception():
                    self._zvm_images.clean_up_snapshot_time_path(
                                                          snapshot_time_path)

        source_image = "".join([zvmutils.get_host(), ":", image_bundle])
        disk_info = {
            'disk_type': CONF.zvm_diskpool_type,
            'disk_source_mn': CONF.zvm_xcat_server,
            'disk_source_image': source_image,
            'disk_image_name': image_name_xcat,
            'disk_owner': disk_owner,
            'disk_eph_size_old': old_eph_disk_size,
            'disk_eph_size_new': new_eph_disk_size,
            'eph_disk_info': eph_disk_info,
            'shared_image_repo': shared_image_repo,
            }

        return jsonutils.dumps(disk_info)

    def _get_eph_disk_info(self, inst_name):
        user_dict = self._get_user_directory(inst_name)
        exl = ''.join(['MDISK ', CONF.zvm_user_root_vdev])
        eph_disks = [mdisk for mdisk in user_dict
                     if (mdisk.__contains__('MDISK ') and
                         not mdisk.__contains__(exl))]

        eph_disk_info = []
        with zvmutils.expect_invalid_xcat_resp_data(eph_disks):
            for eph in eph_disks:
                eph_l = eph.rpartition(" MDISK ")[2].split(' ')
                eph_disk_info.append({'vdev': eph_l[0],
                                      'size': eph_l[3],
                                      'guest_format': None,
                                      'size_in_units': True,
                                      'device_name': eph_l[0]})

        return eph_disk_info

    def _detach_volume_from_instance(self, instance, block_device_mapping):
        for bd in block_device_mapping:
            connection_info = bd['connection_info']
            mountpoint = bd['mount_device']
            if mountpoint:
                mountpoint = self._format_mountpoint(mountpoint)

            if self.instance_exists(instance['name']):
                zvm_inst = ZVMInstance(instance)
                is_active = zvm_inst.is_reachable()
                try:
                    zvm_inst.detach_volume(self._volumeop, connection_info,
                                           instance, mountpoint, is_active,
                                           rollback=False)
                except exception.ZVMVolumeError:
                    LOG.warn(_LW("Failed to detach volume from %s") %
                             instance['name'], instance=instance)

    def _capture_disk_for_instance(self, context, instance):
        """Capture disk."""
        zvm_inst = ZVMInstance(instance)
        image_name = ''.join('rsz' + instance['name'])
        image_uuid = str(uuid.uuid4())
        image_href = image_uuid.replace('-', '_')

        # Capture
        orig_provmethod = zvm_inst.get_provmethod()
        if orig_provmethod != 'sysclone':
            zvm_inst.update_node_provmethod('sysclone')
        image_name_xcat = self._zvm_images.create_zvm_image(instance,
                                                            image_name,
                                                            image_href)
        if orig_provmethod != 'sysclone':
            zvm_inst.update_node_provmethod(orig_provmethod)

        self._zvm_images.update_last_use_date(image_name_xcat)

        return image_name_xcat

    @contextlib.contextmanager
    def cleanup_xcat_image_for_migration(self, image_name_xcat):
        """Cleanup xcat image that imported by migrate_disk_and_power_off."""
        try:
            yield
        except (nova_exception.MigrationError,
                exception.ZVMBaseException):
            LOG.debug("Cleanup image from xCAT image repository")
            with excutils.save_and_reraise_exception():
                self._zvm_images.delete_image_from_xcat(image_name_xcat)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance,
                         block_device_info=None, power_on=True):
        """Completes a resize, turning on the migrated instance

        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param image_meta: image object returned by nova.image.glance that
                           defines the image from which this instance
                           was created
        """
        disk_info = jsonutils.loads(disk_info)

        source_xcat_mn = disk_info['disk_source_mn'].encode('gbk')
        source_image = disk_info['disk_source_image'].encode('gbk')
        image_name_xcat = disk_info['disk_image_name'].encode('gbk')
        disk_type = disk_info['disk_type'].encode('gbk')
        disk_eph_size_old = disk_info['disk_eph_size_old']
        disk_eph_size_new = disk_info['disk_eph_size_new']
        eph_disk_info = disk_info['eph_disk_info']
        shared_image_repo = disk_info['shared_image_repo']

        old_eph_info = block_device_info['ephemerals']
        new_eph_info = [eph for eph in eph_disk_info
                            if eph['vdev'] != CONF.zvm_user_adde_vdev]

        block_device_info = block_device_info or {}
        block_device_info['ephemerals'] = new_eph_info

        if (len(old_eph_info) == 1 and
                old_eph_info[0]['size'] == disk_eph_size_old):
            # Enlarge the only ephemeral disk
            block_device_info['ephemerals'][0]['size'] = disk_eph_size_new
            block_device_info['ephemerals'][0]['size_in_units'] = False

        old_userid = disk_info['disk_owner'].encode('gbk')
        new_userid = None
        if old_userid.startswith("rsz"):
            new_userid = instance['name']
        else:
            new_userid = ''.join(("rsz", old_userid[3:]))

        same_xcat_mn = source_xcat_mn == CONF.zvm_xcat_server
        if disk_type != CONF.zvm_diskpool_type:
            if same_xcat_mn:
                self._zvm_images.delete_image_from_xcat(image_name_xcat)
            msg = _("Can not migration between different disk type"
                    "current is %(c)s, target is %(t)s") % {'t': disk_type,
                    'c': CONF.zvm_diskpool_type}
            LOG.error(msg, instance=instance)
            raise nova_exception.MigrationError(reason=msg)

        profile = image_name_xcat.split('-')[3]
        source_host, t, image_bundle = source_image.partition(":")
        source_ip = source_host.rpartition("@")[2]
        source_image_time_path = image_bundle.rpartition('/')[0]
        local_ip = self.get_host_ip_addr()

        same_os = local_ip == source_ip

        zhcp = self._get_hcp_info()['hostname']

        new_inst = ZVMInstance(instance)
        instance_path = self._pathutils.get_instance_path(
                            CONF.zvm_host, new_inst._name)
        if same_xcat_mn:
            # Same xCAT MN
            # cleanup networking, will re-configure later
            for vif in network_info:
                self._networkop.clean_mac_switch_host(new_inst._name)

            if not shared_image_repo:
                # cleanup image bundle from source compute node
                if not same_os:
                    utils.execute('ssh', source_host, 'rm', '-rf',
                                  source_image_time_path)
                else:
                    self._pathutils.clean_temp_folder(source_image_time_path)

            # Create a xCAT node poin
            old_instance = self._copy_instance(instance)
            old_instance['name'] = ''.join(('rsz', instance['name']))
            old_inst = ZVMInstance(old_instance)

            with self.cleanup_xcat_image_for_migration(image_name_xcat):
                old_inst.copy_xcat_node(new_inst._name)
                try:
                    new_inst.update_node_def(zhcp, new_userid)
                except exception.ZVMBaseException:
                    with excutils.save_and_reraise_exception():
                        old_inst.delete_xcat_node()
        else:
            # Different xCAT MN
            new_inst.create_xcat_node(zhcp)
            if new_userid != new_inst._name:
                try:
                    new_inst.update_node_def(zhcp, new_userid)
                except exception.ZVMBaseException:
                    with excutils.save_and_reraise_exception():
                        old_inst.delete_xcat_node()

            if not same_os:
                snapshot_time_path = self._pathutils.get_snapshot_time_path()
                dest_image_path = os.path.join(snapshot_time_path,
                                               image_name_xcat + '.tgz')
                utils.execute('scp', source_image, snapshot_time_path)
                utils.execute('ssh', source_host,
                              'rm', '-rf', source_image_time_path)
            else:
                snapshot_time_path = source_image_time_path
                dest_image_path = image_bundle

            try:
                self._zvm_images.put_image_to_xcat(dest_image_path, profile)
            except exception.ZVMImageError:
                with excutils.save_and_reraise_exception():
                    new_inst.delete_xcat_node()

            self._zvm_images.clean_up_snapshot_time_path(snapshot_time_path)

        try:
            # Pre-config network and create zvm userid
            self._preset_instance_network(new_inst._name, network_info)
            new_inst.create_userid(block_device_info, image_meta)

            if disk_eph_size_old == 0 and disk_eph_size_new > 0:
                # Punch ephemeral disk info to the new instance
                zvmutils.process_eph_disk(new_inst._name)

            # Add nic and deploy the image
            self._add_nic_to_instance(new_inst._name, network_info, new_userid)
            self._deploy_root_and_ephemeral(new_inst, image_name_xcat)
        except exception.ZVMBaseException:
            with excutils.save_and_reraise_exception():
                self._zvm_images.delete_image_from_xcat(image_name_xcat)

                if not same_xcat_mn:
                    try:
                        for vif in network_info:
                            self._networkop.clean_mac_switch_host(
                                new_inst._name)
                    except exception.ZVMNetworkError as e:
                        emsg = zvmutils.format_exception_msg(e)
                        LOG.debug('clean_mac_switch_host error: %s' % emsg)

                new_inst.delete_userid(self._get_hcp_info()['nodename'])
                new_inst.delete_xcat_node()

                if same_xcat_mn:
                    new_inst.copy_xcat_node(old_inst._name)
                    old_inst.delete_xcat_node()

                    # re-configure the networking
                    self._reconfigure_networking(new_inst._name, network_info,
                                                 old_userid)

                    if not self._is_nic_granted(new_inst._name):
                        msg = _LW("Failed to bind vswitch")
                        LOG.warn(msg, instance=instance)
                    else:
                        if power_on:
                            new_inst.power_on()

        # Cleanup image from xCAT image repository
        self._zvm_images.delete_image_from_xcat(image_name_xcat)

        bdm = driver.block_device_info_get_mapping(block_device_info)
        try:
            zvmutils.punch_xcat_auth_file(instance_path, new_inst._name)
            new_inst.power_on()
            self._attach_volume_to_instance(context, instance, bdm)

            if not power_on:
                new_inst.power_off()
        except exception.ZVMBaseException:
            with excutils.save_and_reraise_exception():
                self.destroy(context, instance, network_info,
                             block_device_info)
                if same_xcat_mn:
                    new_inst.copy_xcat_node(old_inst._name)
                    old_inst.delete_xcat_node()

    def _reconfigure_networking(self, inst_name, network_info, userid=None):
        self._preset_instance_network(inst_name, network_info)
        self._add_nic_to_instance(inst_name, network_info, userid)
        self._wait_for_nic_update(inst_name)
        self._wait_for_addnic(inst_name)

    def _copy_instance(self, instance):
        keys = ('name', 'image_ref', 'uuid', 'user_id', 'project_id',
                'power_state', 'system_metadata', 'memory_mb', 'vcpus',
                'root_gb', 'ephemeral_gb')

        inst_copy = {}
        for key in keys:
            inst_copy[key] = instance[key]

        return inst_copy

    def _attach_volume_to_instance(self, context, instance,
                                   block_device_mapping):
        for bd in block_device_mapping:
            connection_info = bd['connection_info']
            mountpoint = bd['mount_device']
            self.attach_volume(context, connection_info, instance, mountpoint)

    def _add_nic_to_instance(self, inst_name, network_info, userid=None):
        nic_vdev = CONF.zvm_default_nic_vdev
        zhcpnode = self._get_hcp_info()['nodename']
        for vif in network_info:
            self._networkop.create_nic(zhcpnode, inst_name,
                vif['id'], vif['address'], nic_vdev, userid)
            nic_vdev = str(hex(int(nic_vdev, 16) + 3))[2:]

    def _deploy_root_and_ephemeral(self, instance, image_name_xcat):

        # Update the nodetype
        instance.update_node_info_resize(image_name_xcat)

        # Deploy
        instance.deploy_node(image_name_xcat)

        self._zvm_images.update_last_use_date(image_name_xcat)

    def confirm_migration(self, migration, instance, network_info):
        """Confirms a resize, destroying the source VM."""
        # Point to old instance
        old_instance = self._copy_instance(instance)
        old_instance['name'] = ''.join(('rsz', instance['name']))
        old_inst = ZVMInstance(old_instance)

        if self.instance_exists(old_inst._name):
            # Same xCAT MN:
            self.destroy({}, old_instance)
        else:
            # Different xCAT MN:
            self.destroy({}, instance)
            self._zvm_images.cleanup_image_after_migration(instance['name'])

    def finish_revert_migration(self, context, instance, network_info,
                                block_device_info=None, power_on=True):
        """Finish reverting a resize, powering back on the instance."""
        new_instance = self._copy_instance(instance)
        new_instance['name'] = ''.join(('rsz', instance['name']))
        zvm_inst = ZVMInstance(new_instance)
        bdm = driver.block_device_info_get_mapping(block_device_info)

        if self.instance_exists(zvm_inst._name):
            # Same xCAT MN:
            old_inst = ZVMInstance(instance)
            old_inst.copy_xcat_node(new_instance['name'])
            zvm_inst.delete_xcat_node()

            self._reconfigure_networking(instance['name'], network_info)

            if not self._is_nic_granted(instance['name']):
                msg = _("Failed to bind vswitch")
                LOG.error(msg, instance=instance)
                raise nova_exception.MigrationError(reason=msg)
        else:
            # Different xCAT MN:
            self._zvm_images.cleanup_image_after_migration(instance['name'])

        self._attach_volume_to_instance({}, instance, bdm)

        if power_on:
            self.power_on({}, instance, [])

    def _wait_for_addnic(self, inst_name):
        """Wait until quantum adding NIC done."""

        def _wait_addnic(inst_name, expiration):
            if (CONF.zvm_reachable_timeout and
                    timeutils.utcnow() > expiration):
                raise loopingcall.LoopingCallDone()

            is_done = False
            try:
                is_done = self._is_nic_granted(inst_name)
            except exception.ZVMBaseException:
                # Ignore any zvm driver exceptions
                return

            if is_done:
                LOG.debug("NIC added and granted to vswitch correctly")
                raise loopingcall.LoopingCallDone()

        expiration = timeutils.utcnow() + datetime.timedelta(
                         seconds=CONF.zvm_reachable_timeout)

        timer = loopingcall.FixedIntervalLoopingCall(_wait_addnic, inst_name,
                                                     expiration)
        timer.start(interval=10).wait()

    def _get_user_directory(self, inst_name):
        url = self._xcat_url.lsvm('/' + inst_name)
        user_dict = zvmutils.xcat_request("GET", url)

        with zvmutils.expect_invalid_xcat_resp_data(user_dict):
            dict_str = user_dict['info'][0][0]

        return dict_str.split("\n")

    def _is_nic_granted(self, inst_name):
        dict_list = self._get_user_directory(inst_name)
        _all_granted = False
        for rec in dict_list:
            if " NICDEF " in rec:
                _all_granted = " LAN SYSTEM " in rec
                if not _all_granted:
                    return False

        return _all_granted

    def set_admin_password(self, instance, new_pass=None):
        """Set the root password on the specified instance.

        The first parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name. The second
        parameter is the value of the new password.
        """
        if new_pass is not None:
            self._set_admin_password(instance['name'], new_pass)

    def _set_admin_password(self, inst_name, password):
        command = "echo %s|passwd --stdin root" % password
        try:
            zvmutils.xdsh(inst_name, command)
        except exception.ZVMXCATXdshFailed as err:
            LOG.error(_("Setting root password for instance %(instance)s "
                        "failed with reason: %(err)s") %
                      {'instance': inst_name, 'err': err.format_message()})
            raise err

    def _wait_for_nic_update(self, inst_name):
        """Wait until NIC definition is updated."""

        def _wait_revoke(inst_name, expiration):
            """Wait until NIC is uncoupled from vswitch."""
            if (CONF.zvm_reachable_timeout and
                    timeutils.utcnow() > expiration):
                LOG.warn(_LW("NIC update check failed."))
                raise loopingcall.LoopingCallDone()

            is_granted = True
            try:
                is_granted = self._is_nic_granted(inst_name)
            except exception.ZVMBaseException:
                # Ignore any zvm driver exceptions
                return

            if not is_granted:
                LOG.debug("NIC has been updated")
                raise loopingcall.LoopingCallDone()

        expiration = timeutils.utcnow() + datetime.timedelta(
                         seconds=CONF.zvm_reachable_timeout)

        timer = loopingcall.FixedIntervalLoopingCall(_wait_revoke, inst_name,
                                                     expiration)
        timer.start(interval=10).wait()

        return

    def get_console_output(self, context, instance):
        # Because bug 534, in Juno, xcat will make new instance punch
        # console logs to ZHCP and it violate the current RACF setting
        # plus we don't announce support this feature, temply disable it
        # until we can solve that RACF issue.
        # in nova/api/openstack/compute/contrib/console_output.py
        # it will catch the exception like:
        # except NotImplementedError:
        #   msg = _("Unable to get console log, functionality not implemented")
        #   raise webob.exc.HTTPNotImplemented(explanation=msg)
        raise NotImplementedError

    def _get_console_output(self, context, instance):
        """Get console output for an instance."""

        def append_to_log(log_data, log_path):
            LOG.debug('log_data: %(log_data)r, log_path: %(log_path)r',
                         {'log_data': log_data, 'log_path': log_path})
            fp = open(log_path, 'a+')
            fp.write(log_data)
            return log_path

        zvm_inst = ZVMInstance(instance)
        logsize = CONF.zvm_console_log_size * units.Ki
        console_log = ""
        try:
            console_log = zvm_inst.get_console_log(logsize)
        except exception.ZVMXCATInternalError:
            # Ignore no console log avaiable error
            LOG.warn(_LW("No new console log avaiable."))
        log_path = self._pathutils.get_console_log_path(CONF.zvm_host,
                       zvm_inst._name)
        append_to_log(console_log, log_path)

        log_fp = file(log_path, 'rb')
        log_data, remaining = utils.last_bytes(log_fp, logsize)
        if remaining > 0:
            LOG.info(_LI('Truncated console log returned, %d bytes ignored'),
                remaining, instance=instance)

        return log_data

    def get_host_uptime(self):
        """Get host uptime."""
        with zvmutils.expect_invalid_xcat_resp_data(self._host_stats):
            return self._host_stats[0]['ipl_time']

    def get_available_nodes(self, refresh=False):
        return [d['hypervisor_hostname'] for d in self._host_stats
                if (d.get('hypervisor_hostname') is not None)]

    def _extract_volume_id(self, bdm, root_device):
        for bd in bdm:
            mount_point = bd['mount_device']
            is_root = zvmutils.is_volume_root(root_device, mount_point)
            if is_root:
                return bd['connection_info']['serial']

        errmsg = _("Failed to extract volume id from block device mapping."
                   "%s") % str(bdm)
        raise exception.ZVMDriverError(msg=errmsg)

    def _exclude_root_volume_bdm(self, bdm, root_mount_device):
        for bd in bdm:
            mountpoint = bd['mount_device']
            is_root = zvmutils.is_volume_root(root_mount_device, mountpoint)
            if is_root:
                bdm.remove(bd)
        return bdm

    def _get_xcat_version(self):
        url = self._xcat_url.version()
        version_info = zvmutils.xcat_request("GET", url)
        with zvmutils.expect_invalid_xcat_resp_data(version_info):
            dict_str = version_info['data'][0][0]
            version = dict_str.split()[1]
            version = versionutils.convert_version_to_int(version)
        return version

    def _version_check(self, req_ver=None, op=operator.lt):
        try:
            if req_ver is not None:
                cur_ver = self._get_xcat_version()
                if op(cur_ver, versionutils.convert_version_to_int(req_ver)):
                    return False
            return True
        except Exception:
            return False

    def has_min_version(self, req_ver=None):
        return self._version_check(req_ver=req_ver, op=operator.lt)

    def has_version(self, req_ver=None):
        return self._version_check(req_ver=req_ver, op=operator.ne)
