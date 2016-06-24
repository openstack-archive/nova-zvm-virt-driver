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


import datetime
import os
import re
import shutil
import tarfile
import xml.dom.minidom as Dom

from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils

from nova import exception as nova_exception
from nova.i18n import _, _LW
from nova.image import glance
from nova import utils
from nova.virt import images
from nova.virt.zvm import const
from nova.virt.zvm import exception
from nova.virt.zvm import utils as zvmutils

LOG = logging.getLogger(__name__)
CONF = cfg.CONF
QUEUE_BUFFER_SIZE = 10


class ZVMImages(object):

    def __init__(self):
        self._xcat_url = zvmutils.XCATUrl()
        self._pathutils = zvmutils.PathUtils()

    def create_zvm_image(self, instance, image_name, image_href):
        """Create z/VM image from z/VM instance by invoking xCAT REST API
        imgcapture.
        """
        nodename = instance['name']
        profile = image_name + "_" + image_href.replace('-', '_')
        body = ['nodename=' + nodename,
                'profile=' + profile]
        if CONF.zvm_image_compression_level:
            if CONF.zvm_image_compression_level.isdigit() and (
                int(CONF.zvm_image_compression_level) in range(0, 10)):
                body.append('compress=%s' % CONF.zvm_image_compression_level)
            else:
                msg = _("Invalid zvm_image_compression_level detected, please"
                "specify it with a integer between 0 and 9 in your nova.conf")
                raise exception.ZVMImageError(msg=msg)

        url = self._xcat_url.imgcapture()
        LOG.debug('Capturing %s start' % instance['name'])

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMImageError):
            res = zvmutils.xcat_request("POST", url, body)

        os_image = self._get_os_image(res)

        return os_image

    def _get_os_image(self, response):
        """Return the image_name by parsing the imgcapture rest api
        response.
        """
        image_name_xcat = ""
        if len(response['info']) > 0:
            for info in response['info']:
                for info_element in info:
                    if "Completed capturing the image" in info_element:
                        start_index = info_element.find('(')
                        end_index = info_element.find(')')
                        image_name_xcat = info_element[start_index + 1:
                                                       end_index]
                        return image_name_xcat
            if len(image_name_xcat) == 0:
                msg = _("Capture image failed.")
                LOG.error(msg)
                raise exception.ZVMImageError(msg=msg)
        else:
            msg = _("Capture image returns bad response.")
            LOG.error(msg)
            raise exception.ZVMImageError(msg=msg)

    def get_snapshot_time_path(self):
        return self._pathutils.get_snapshot_time_path()

    def get_image_from_xcat(self, image_name_xcat, image_name,
                            snapshot_time_path):
        """Import image from xCAT to nova, by invoking the imgexport
        REST API.
        """
        LOG.debug("Getting image from xCAT")
        destination = os.path.join(snapshot_time_path, image_name + '.tgz')
        host = zvmutils.get_host()
        body = ['osimage=' + image_name_xcat,
                'destination=' + destination,
                'remotehost=' + host]
        url = self._xcat_url.imgexport()

        try:
            zvmutils.xcat_request("POST", url, body)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError) as err:
            msg = (_("Transfer image to compute node failed: %s") %
                   err.format_message())
            raise exception.ZVMImageError(msg=msg)
        return destination

    def delete_image_glance(self, image_service, context, image_href):
        """Delete the image from glance database and image repository.

        Can be used for a rollback step if operations to image fail.
        """
        image_service.delete(context, image_href)

    def clean_up_snapshot_time_path(self, snapshot_time_path):
        """Clean up the time_path and its contents under "snapshot_tmp" after
        image uploaded to glance.

        Also be used for a rollback step if operations to the image file fail.
        """
        if os.path.exists(snapshot_time_path):
            LOG.debug("Cleaning up nova local image file")
            shutil.rmtree(snapshot_time_path)

    def _delete_image_file_from_xcat(self, image_name_xcat):
        """Delete image file from xCAT MN.

        When capturing, image in the xCAT MN's repository will be removed after
        it is imported to nova compute node.
        """
        LOG.debug("Removing image files from xCAT MN image repository")
        url = self._xcat_url.rmimage('/' + image_name_xcat)
        try:
            zvmutils.xcat_request("DELETE", url)
        except (exception.ZVMXCATInternalError,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATRequestFailed):
            LOG.warn(_LW("Failed to delete image file %s from xCAT") %
                     image_name_xcat)

    def _delete_image_object_from_xcat(self, image_name_xcat):
        """Delete image object from xCAT MN.

        After capturing, image definition in the xCAT MN's table osimage and
        linuximage will be removed after it is imported to nova compute node.

        """
        LOG.debug("Deleting the image object")
        url = self._xcat_url.rmobject('/' + image_name_xcat)
        try:
            zvmutils.xcat_request("DELETE", url)
        except (exception.ZVMXCATInternalError,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATRequestFailed):
            LOG.warn(_LW("Failed to delete image definition %s from xCAT") %
                     image_name_xcat)

    def get_image_file_path_from_image_name(self, image_name_xcat):
        return zvmutils.xcat_cmd_gettab("linuximage", "imagename",
                                        image_name_xcat, "rootimgdir")

    def delete_image_from_xcat(self, image_name_xcat):
        self._delete_image_file_from_xcat(image_name_xcat)
        self._delete_image_object_from_xcat(image_name_xcat)

    def _getxmlnode(self, node, name):
        return node.getElementsByTagName(name)[0] if node else []

    def _getnode(self, node_root, tagname):
        """For parse manifest."""
        nodename = node_root.getElementsByTagName(tagname)[0]
        nodevalue = nodename.childNodes[0].data
        return nodevalue

    def parse_manifest_xml(self, image_package_path):
        """Return the image properties from manifest.xml."""
        LOG.debug("Parsing the manifest.xml")
        manifest_xml = os.path.join(image_package_path, "manifest.xml")

        manifest = {}

        if os.path.exists(manifest_xml):
            xml_file = Dom.parse(manifest_xml)
        else:
            LOG.warn(_LW('manifest.xml does not exist'))
            manifest['imagename'] = ''
            manifest['imagetype'] = ''
            manifest['osarch'] = ''
            manifest['osname'] = ''
            manifest['osvers'] = ''
            manifest['profile'] = ''
            manifest['provmethod'] = ''
            return manifest

        node_root = xml_file.documentElement
        node_root = self._getxmlnode(node_root, 'osimage')
        manifest['imagename'] = self._getnode(node_root, "imagename")
        manifest['imagetype'] = self._getnode(node_root, "imagetype")
        manifest['osarch'] = self._getnode(node_root, "osarch")
        manifest['osname'] = self._getnode(node_root, "osname")
        manifest['osvers'] = self._getnode(node_root, "osvers")
        manifest['profile'] = self._getnode(node_root, "profile")
        manifest['provmethod'] = self._getnode(node_root, "provmethod")

        return manifest

    def untar_image_bundle(self, snapshot_time_path, image_bundle):
        """Untar the image bundle *.tgz from xCAT and remove the *.tgz."""
        if os.path.exists(image_bundle):
            LOG.debug("Untarring the image bundle ... ")
            tarobj = tarfile.open(image_bundle, "r:gz")
            for tarinfo in tarobj:
                tarobj.extract(tarinfo.name, path=snapshot_time_path)
            tarobj.close()
            os.remove(image_bundle)
        else:
            self.clean_up_snapshot_time_path(snapshot_time_path)
            msg = _("Image bundle does not exist %s") % image_bundle
            raise exception.ZVMImageError(msg=msg)

    def get_image_file_name(self, image_package_path):
        if os.path.exists(image_package_path):
            file_contents = os.listdir(image_package_path)
            for f in file_contents:
                if f.endswith('.img'):
                    return f
            msg = _("Can not find image file under %s") % image_package_path
        else:
            msg = _("Image path %s not exist") % image_package_path
        raise exception.ZVMImageError(msg=msg)

    def image_exist_xcat(self, image_id):
        """To see if the specific image exist in xCAT MN's image
        repository.
        """
        LOG.debug("Checking if the image %s exists or not in xCAT "
                    "MN's image repository " % image_id)
        image_uuid = image_id.replace('-', '_')
        parm = '&criteria=profile=~' + image_uuid
        url = self._xcat_url.lsdef_image(addp=parm)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMImageError):
            res = zvmutils.xcat_request("GET", url)

        res_image = res['info']

        if '_' in str(res_image):
            return True
        else:
            return False

    def fetch_image(self, context, image_id, target, user, project):
        LOG.debug("Downloading image %s from glance image server" %
                  image_id)
        try:
            images.fetch(context, image_id, target)
        except Exception as err:
            emsg = zvmutils.format_exception_msg(err)
            msg = _("Download image file of image %(id)s failed with reason:"
                    " %(err)s") % {'id': image_id, 'err': emsg}
            raise exception.ZVMImageError(msg=msg)

    def generate_manifest_file(self, image_meta, image_name, disk_file,
                               manifest_path):
        """Generate the manifest.xml file from glance's image metadata
        as a part of the image bundle.
        """
        image_id = image_meta['id']
        image_type = image_meta['properties']['image_type_xcat']
        os_version = image_meta['properties']['os_version']
        os_name = image_meta['properties']['os_name']
        os_arch = image_meta['properties']['architecture']
        prov_method = image_meta['properties']['provisioning_method']

        image_profile = '_'.join((image_name, image_id.replace('-', '_')))
        image_name_xcat = '-'.join((os_version, os_arch,
                               prov_method, image_profile))
        rootimgdir_str = ('/install', prov_method, os_version,
                          os_arch, image_profile)
        rootimgdir = '/'.join(rootimgdir_str)
        today_date = datetime.date.today()
        last_use_date_string = today_date.strftime("%Y-%m-%d")
        is_deletable = "auto:last_use_date:" + last_use_date_string

        doc = Dom.Document()
        xcatimage = doc.createElement('xcatimage')
        doc.appendChild(xcatimage)

        # Add linuximage section
        imagename = doc.createElement('imagename')
        imagename_value = doc.createTextNode(image_name_xcat)
        imagename.appendChild(imagename_value)
        rootimagedir = doc.createElement('rootimgdir')
        rootimagedir_value = doc.createTextNode(rootimgdir)
        rootimagedir.appendChild(rootimagedir_value)
        linuximage = doc.createElement('linuximage')
        linuximage.appendChild(imagename)
        linuximage.appendChild(rootimagedir)
        xcatimage.appendChild(linuximage)

        # Add osimage section
        osimage = doc.createElement('osimage')
        manifest = {'imagename': image_name_xcat,
                    'imagetype': image_type,
                    'isdeletable': is_deletable,
                    'osarch': os_arch,
                    'osname': os_name,
                    'osvers': os_version,
                    'profile': image_profile,
                    'provmethod': prov_method}

        for item in list(manifest.keys()):
            itemkey = doc.createElement(item)
            itemvalue = doc.createTextNode(manifest[item])
            itemkey.appendChild(itemvalue)
            osimage.appendChild(itemkey)
            xcatimage.appendChild(osimage)
            f = open(manifest_path + '/manifest.xml', 'w')
            f.write(doc.toprettyxml(indent=''))
            f.close()

        # Add the rawimagefiles section
        rawimagefiles = doc.createElement('rawimagefiles')
        xcatimage.appendChild(rawimagefiles)

        files = doc.createElement('files')
        files_value = doc.createTextNode(rootimgdir + '/' + disk_file)
        files.appendChild(files_value)

        rawimagefiles.appendChild(files)

        f = open(manifest_path + '/manifest.xml', 'w')
        f.write(doc.toprettyxml(indent='  '))
        f.close()

        self._rewr(manifest_path)

        return manifest_path + '/manifest.xml'

    def _rewr(self, manifest_path):
        f = open(manifest_path + '/manifest.xml', 'r')
        lines = f.read()
        f.close()

        lines = lines.replace('\n', '')
        lines = re.sub(r'>(\s*)<', r'>\n\1<', lines)
        lines = re.sub(r'>[ \t]*(\S+)[ \t]*<', r'>\1<', lines)

        f = open(manifest_path + '/manifest.xml', 'w')
        f.write(lines)
        f.close()

    def generate_image_bundle(self, spawn_path, tmp_file_fn, image_name):
        """Generate the image bundle which is used to import to xCAT MN's
        image repository.
        """
        image_bundle_name = image_name + '.tar'
        tar_file = spawn_path + '/' + tmp_file_fn + '_' + image_bundle_name
        LOG.debug("The generate the image bundle file is %s" % tar_file)

        os.chdir(spawn_path)
        tarFile = tarfile.open(tar_file, mode='w')

        try:
            tarFile.add(tmp_file_fn)
            tarFile.close()
        except Exception as err:
            msg = (_("Generate image bundle failed: %s") % err)
            LOG.error(msg)
            if os.path.isfile(tar_file):
                os.remove(tar_file)
            raise exception.ZVMImageError(msg=msg)
        finally:
            self._pathutils.clean_temp_folder(tmp_file_fn)

        return tar_file

    def check_space_imgimport_xcat(self, context, instance, tar_file,
                                   xcat_free_space_threshold, zvm_xcat_master):
        image_href = instance['image_ref']
        try:
            free_space_xcat = self.get_free_space_xcat(
                                  xcat_free_space_threshold, zvm_xcat_master)
            img_transfer_needed = self._get_transfer_needed_space_xcat(context,
                                      image_href, tar_file)
            larger = max(xcat_free_space_threshold, img_transfer_needed)
            if img_transfer_needed > free_space_xcat:
                larger = max(xcat_free_space_threshold, img_transfer_needed)
                size_needed = float(larger - free_space_xcat)
                self.prune_image_xcat(context, size_needed,
                                      img_transfer_needed)
            else:
                LOG.debug("Image transfer needed space satisfied in xCAT")
        except exception.ZVMImageError:
            with excutils.save_and_reraise_exception():
                os.remove(tar_file)

    def put_image_to_xcat(self, image_bundle_package, image_profile):
        """Import the image bundle from compute node to xCAT MN's image
        repository.
        """
        remote_host_info = zvmutils.get_host()
        body = ['osimage=%s' % image_bundle_package,
                'profile=%s' % image_profile,
                'remotehost=%s' % remote_host_info,
                'nozip']
        url = self._xcat_url.imgimport()

        try:
            zvmutils.xcat_request("POST", url, body)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError) as err:
            msg = (_("Import the image bundle to xCAT MN failed: %s") %
                   err.format_message())
            raise exception.ZVMImageError(msg=msg)
        finally:
            os.remove(image_bundle_package)

    def get_imgname_xcat(self, image_id):
        """Get the xCAT deployable image name by image id."""
        image_uuid = image_id.replace('-', '_')
        parm = '&criteria=profile=~' + image_uuid
        url = self._xcat_url.lsdef_image(addp=parm)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMImageError):
            res = zvmutils.xcat_request("GET", url)
            with zvmutils.expect_invalid_xcat_resp_data(res):
                res_image = res['info'][0][0]
                res_img_name = res_image.strip().split(" ")[0]

        if res_img_name:
            return res_img_name
        else:
            LOG.error(_("Fail to find the right image to deploy"))

    def _get_image_list_xcat(self):
        """Get an image list from xcat osimage table.

        criteria: osarch=s390x and provmethod=netboot|raw|sysclone and
        isdeletable field

        """
        display_field = '&field=isdeletable'
        isdeletable_criteria = '&criteria=isdeletable=~^auto:last_use_date:'
        osarch_criteria = '&criteria=osarch=s390x'
        provmethod_criteria = '&criteria=provmethod=~netboot|raw|sysclone'

        addp = ''.join([isdeletable_criteria, osarch_criteria,
                       provmethod_criteria, display_field])
        url = self._xcat_url.lsdef_image(addp=addp)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMImageError):
            output = zvmutils.xcat_request("GET", url)

        image_list = []
        if len(output['info']) <= 0:
            return image_list

        if len(output['info'][0]) > 0:
            i = 0
            while i < len(output['info'][0]):
                if "Object name:" in output['info'][0][i]:
                    sub_list = []
                    len_objectname = len("Object name: ")
                    is_deletable = output['info'][0][i + 1]
                    last_use_date = self._validate_last_use_date(
                                        output['info'][0][i][len_objectname:],
                                        is_deletable)
                    if last_use_date is None:
                        i += 2
                        continue
                    sub_list.append(output['info'][0][i][len_objectname:])
                    sub_list.append(last_use_date)
                    image_list.append(sub_list)
                i += 2

        return image_list

    def update_last_use_date(self, image_name_xcat):
        """Update the last_use_date in xCAT osimage table after a
        successful deploy.
        """
        LOG.debug("Update the last_use_date in xCAT osimage table "
                    "after a successful deploy")

        today_date = datetime.date.today()
        last_use_date_string = today_date.strftime("%Y-%m-%d")
        url = self._xcat_url.tabch('/osimage')
        is_deletable = "auto:last_use_date:" + last_use_date_string
        body = ["imagename=" + image_name_xcat,
                "osimage.isdeletable=" + is_deletable]

        try:
            zvmutils.xcat_request("PUT", url, body)
        except (exception.ZVMXCATRequestFailed,
                exception.ZVMInvalidXCATResponseDataError,
                exception.ZVMXCATInternalError) as err:
            LOG.warn(_LW("Illegal date for last_use_date %s") %
                     err.format_message())

        return last_use_date_string

    def _validate_last_use_date(self, image_name, is_deletable):
        """Validate the isdeletable date format."""
        last_use_date_string = is_deletable.split(":")[2]
        timere = ("^\d{4}[-]((0([1-9]{1}))|"
                  "(1[0|1|2]))[-](([0-2]([0-9]{1}))|(3[0|1]))$")

        if (len(last_use_date_string) == 10) and (
                re.match(timere, last_use_date_string)):
            LOG.debug("The format for last_use_date is valid ")
        else:
            LOG.warn(_LW("The format for image %s record in xcat table "
                "osimage's last_used_date is not valid. The correct "
                "format is auto:last_use_date:yyyy-mm-dd") % image_name)
            return

        try:
            last_use_date_datetime = datetime.datetime.strptime(
                                    last_use_date_string, '%Y-%m-%d')
        except Exception as err:
            LOG.warn(_LW("Illegal date for last_use_date %(msg)s") % err)
            return

        return last_use_date_datetime.date()

    def _verify_is_deletable_periodic(self, last_use_date, clean_period):
        """Check the last_use_date of an image to determine if the image
        need to be cleaned.
        """
        now = datetime.date.today()
        delta = (now - last_use_date).days
        if (delta - clean_period) >= 0:
            return True
        else:
            return False

    def clean_image_cache_xcat(self, clean_period):
        """Clean the old image."""
        image_list = self._get_image_list_xcat()
        if len(image_list) <= 0:
            return
        else:
            i = 0
            while i < len(image_list):
                image_name_xcat = image_list[i][0]
                last_use_date = image_list[i][1]
                if self._verify_is_deletable_periodic(last_use_date,
                                                   clean_period):
                    LOG.debug('Delete the image %s' % image_name_xcat)
                    self.delete_image_from_xcat(image_name_xcat)
                else:
                    LOG.debug("Keep the image")
                i += 1

    def _get_image_bundle_size(self, tar_file):
        size_byte = os.path.getsize(tar_file)
        return float(size_byte) / 1024 / 1024 / 1024

    def _get_image_size_glance(self, context, image_href):
        (image_service, image_id) = glance.get_remote_image_service(
            context, image_href)

        try:
            image_meta = image_service.show(context, image_href)
        except nova_exception.ImageNotFound:
            image_meta = {}
            return 0

        size_byte = image_meta['size']
        return float(size_byte) / 1024 / 1024 / 1024

    def get_free_space_xcat(self, xcat_free_space_threshold, zvm_xcat_master):
        """Get the free space in xCAT MN /install."""
        LOG.debug("Get the xCAT MN /install free space")
        addp = "&field=--freerepospace"
        if isinstance(zvm_xcat_master, str):
            url = self._xcat_url.rinv("/" + zvm_xcat_master, addp=addp)
        else:
            msg = _("zvm_xcat_master should be specified as a string")
            LOG.error(msg)
            raise exception.ZVMImageError(msg=msg)

        with zvmutils.except_xcat_call_failed_and_reraise(
                exception.ZVMImageError):
            result = zvmutils.xcat_request("GET", url)
            with zvmutils.expect_invalid_xcat_resp_data(result):
                if len(result['info']) == 0:
                    msg = _("'rinv <zvm_xcat_master> --freerepospace' returns "
                            "null, please check 'df -h /install', there may "
                            "be something wrong with the mount of /install")
                    raise exception.ZVMImageError(msg=msg)
                free_space_line = result['info'][0][0]
                free_space = free_space_line.split()[4]
                if free_space.endswith("G"):
                    free_disk_value = free_space.rstrip("G")
                    return float(free_disk_value)
                elif free_space.endswith("M"):
                    free_disk_value = free_space.rstrip("M")
                    return float(free_disk_value) / 1024
                elif free_space == "0":
                    return 0
                else:
                    return xcat_free_space_threshold

    def get_imgcapture_needed(self, instance):
        """Get the space needed on xCAT MN for an image capture."""
        LOG.debug("Getting image capture needed size for %s" %
                  instance['name'])

        cmd = "df -h /"
        result = None
        result = zvmutils.xdsh(instance['name'], cmd)['data'][0]
        imgcapture_needed_space = ""
        try:
            result_data = result[0].split()
            if (CONF.zvm_image_compression_level and
                    int(CONF.zvm_image_compression_level) == 0):
                imgcapture_needed_space = result_data[10]
            else:
                imgcapture_needed_space = result_data[11]

            if imgcapture_needed_space.endswith("G"):
                imgcapture_needed_space_value = imgcapture_needed_space.rstrip(
                                                                        "G")
                return float(imgcapture_needed_space_value) * 2
            elif imgcapture_needed_space.endswith("M"):
                imgcapture_needed_space_value = imgcapture_needed_space.rstrip(
                                                                        "M")
                return (float(imgcapture_needed_space_value) / 1024) * 2
            else:
                return const.ZVM_IMAGE_SIZE_MAX
        except (IndexError, ValueError, TypeError) as err:
            raise exception.ZVMImageError(msg=err)

    def _get_transfer_needed_space_xcat(self, context, image_href, tar_file):
        """To transfer an image bundle from glance to xCAT, the needed size is
        image_bundle_size + image_size.
        """
        image_bundle_size = self._get_image_bundle_size(tar_file)
        image_size = self._get_image_size_glance(context, image_href)
        return image_bundle_size + image_size

    def _get_image_href_by_osimage(self, image_name_xcat):
        """If we have the xCAT.osimage.imagename, we want to get the
        image_href.
        """
        try:
            image_profile = image_name_xcat.split("-")
            if len(image_profile) >= 4:
                return image_profile[3]
            else:
                return image_name_xcat
        except (TypeError, IndexError):
            LOG.error(_("xCAT imagename format for %s is not as expected")
                      % image_name_xcat)

    def _sort_image_by_use_date(self, image_list, left, right):
        """Sort the image_list by last_use_date from oldest image to latest."""
        if (left < right):
            i = left
            j = right
            x = image_list[left]
            while (i < j):
                while (i < j and image_list[j][1] >= x[1]):
                    j -= 1
                if(i < j):
                    image_list[i] = image_list[j]
                    i += 1
                while(i < j and image_list[i][1] < x[1]):
                    i += 1
                if(i < j):
                    image_list[j] = image_list[i]
                    j -= 1
            image_list[i] = x
            self._sort_image_by_use_date(image_list, left, i - 1)
            self._sort_image_by_use_date(image_list, i + 1, right)

    def _get_to_be_deleted_images_xcat(self, context, size_needed,
                                      current_needed):
        """To get a list of images which is to be removed from xCAT image
        repository because it cannot provide enough space for image operations
        from OpenStack.
        """
        image_list = self._get_image_list_xcat()
        size_sum = 0
        to_be_deleted_image_profile = []

        if len(image_list) <= 0:
            msg = _("No image to be deleted, please create space manually "
                    "on xcat(%s).") % CONF.zvm_xcat_server
            raise exception.ZVMImageError(msg=msg)
        else:
            self._sort_image_by_use_date(image_list, 0, len(image_list) - 1)
            for img in image_list:
                image_name_xcat = img[0]
                image_profile = self._get_image_href_by_osimage(
                                    image_name_xcat)
                image_uuid = image_profile.partition('_')[2].replace("_", "-")
                image_size = self._get_image_size_glance(context,
                                                         image_uuid)
                if image_size > 0:
                    to_be_deleted_image_profile.append(image_profile)
                    size_sum += image_size
                    if size_sum >= size_needed:
                        return to_be_deleted_image_profile

        if size_sum >= current_needed:
            return to_be_deleted_image_profile
        else:
            msg = _("xCAT MN space not enough for current image operation: "
                    "%(n)d G needed,%(a)d G available") % {'n': current_needed,
                                                           'a': size_sum}
            raise exception.ZVMImageError(msg=msg)

    def prune_image_xcat(self, context, size_needed, current_needed):
        """Remove the images which meet remove criteria from xCAT."""
        LOG.debug("Clear up space by clean images in xCAT")
        to_be_deleted_image_profile = self._get_to_be_deleted_images_xcat(
            context, size_needed, current_needed)
        if len(to_be_deleted_image_profile) > 0:
            for image_profile in to_be_deleted_image_profile:
                image_name_xcat = self.get_imgname_xcat(image_profile)
                self.delete_image_from_xcat(image_name_xcat)

    def zimage_check(self, image_meta):
        """Do a brief check to see if the image is a valid zVM image."""
        property_ = ['image_file_name', 'image_type_xcat', 'architecture',
                    'os_name', 'provisioning_method', 'os_version']
        missing_prop = []
        for prop in property_:
            if prop not in list(image_meta['properties'].keys()):
                missing_prop.append(prop)

        if len(missing_prop) > 0:
            msg = (_("The image %(id)s is not a valid zVM image, "
                   "property %(prop)s are missing") % {'id': image_meta['id'],
                                                       'prop': missing_prop})
            LOG.error(msg)
            raise exception.ZVMImageError(msg=msg)

    def cleanup_image_after_migration(self, inst_name):
        """Cleanup osimages in xCAT image repository while confirm migration
        or revert migration at source compute.
        """
        image_list = self._get_image_list_xcat()
        matchee = ''.join(['rsz', inst_name])
        for img in image_list:
            img_name = img[0]
            if matchee in img_name:
                self.delete_image_from_xcat(img_name)

    def get_root_disk_units(self, image_file_path):
        """use 'hexdump' to get the root_disk_units."""
        try:
            (output, _toss) = utils.execute('hexdump', '-n', '48',
                '-C', image_file_path)
        except processutils.ProcessExecutionError:
            msg = (_("Get image property failed,"
                    " please check whether the image file exists!"))
            raise exception.ZVMImageError(msg=msg)

        LOG.debug("hexdump result is %s" % output)
        try:
            root_disk_units = int(output[144:156])
        except ValueError:
            msg = (_("Image file at %s is missing imbeded disk size "
                    "metadata, it was probably not captured with xCAT")
                    % image_file_path)
            raise exception.ZVMImageError(msg=msg)

        if 'FBA' not in output and 'CKD' not in output:
            msg = (_("The image's disk type is not valid. Currently we only"
                      " support FBA and CKD disk"))
            raise exception.ZVMImageError(msg=msg)

        LOG.debug("The image's root_disk_units is %s" % root_disk_units)
        return root_disk_units

    def set_image_root_disk_units(self, context, image_meta, image_file_path):
        """Set the property 'root_disk_units'to image."""
        new_image_meta = image_meta
        root_disk_units = self.get_root_disk_units(image_file_path)
        LOG.debug("The image's root_disk_units is %s" % root_disk_units)

        (glance_image_service, image_id) = glance.get_remote_image_service(
                                                context, image_meta['id'])
        new_image_meta = glance_image_service.show(context, image_id)
        new_image_meta['properties']['root_disk_units'] = str(root_disk_units)

        try:
            glance_image_service.update(context, image_id,
                                        new_image_meta, None)
        except nova_exception.ImageNotAuthorized:
            msg = _('Not allowed to modify attributes for image %s') % image_id
            LOG.error(msg)

        return new_image_meta

    def get_image_menifest(self, image_name_xcat):
        """Get image manifest info from xcat osimage table."""
        attr_list = ['imagetype', 'osarch', 'imagename', 'osname',
                     'osvers', 'profile', 'provmethod']
        menifest = zvmutils.xcat_cmd_gettab_multi_attr('osimage', 'imagename',
                                                    image_name_xcat, attr_list)
        return menifest
