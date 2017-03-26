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
import functools
import os
import pwd
import shutil
import six
from six.moves import http_client as httplib
import socket
import ssl
import time

from nova import block_device
from nova.compute import power_state
from nova import exception as nova_exception
from nova.i18n import _, _LE, _LI, _LW
from nova.virt import driver
from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_utils import excutils

from nova_zvm.virt.zvm import const
from nova_zvm.virt.zvm import dist
from nova_zvm.virt.zvm import exception


LOG = logging.getLogger(__name__)

CONF = cfg.CONF
CONF.import_opt('instances_path', 'nova.compute.manager')


_XCAT_URL = None


class XCATUrl(object):
    """To return xCAT URLs for invoking xCAT REST APIs."""

    def __init__(self):
        """Set constants used to form xCAT URLs."""
        self.PREFIX = '/xcatws'
        self.SUFFIX = ('?userName=' + CONF.zvm_xcat_username +
                      '&password=' + CONF.zvm_xcat_password +
                      '&format=json')

        self.NODES = '/nodes'
        self.VMS = '/vms'
        self.IMAGES = '/images'
        self.OBJECTS = '/objects/osimage'
        self.OS = '/OS'
        self.TABLES = '/tables'
        self.HV = '/hypervisor'
        self.NETWORK = '/networks'

        self.POWER = '/power'
        self.INVENTORY = '/inventory'
        self.STATUS = '/status'
        self.MIGRATE = '/migrate'
        self.CAPTURE = '/capture'
        self.EXPORT = '/export'
        self.IMGIMPORT = '/import'
        self.BOOTSTAT = '/bootstate'
        self.XDSH = '/dsh'
        self.VERSION = '/version'
        self.PCONTEXT = '&requestid='
        self.PUUID = '&objectid='
        self.DIAGLOGS = '/logs/diagnostics'
        self.PNODERANGE = '&nodeRange='
        self.EXECCMDONVM = '/execcmdonvm'

    def _nodes(self, arg=''):
        return self.PREFIX + self.NODES + arg + self.SUFFIX

    def _vms(self, arg='', vmuuid='', context=None):
        rurl = self.PREFIX + self.VMS + arg + self.SUFFIX
        rurl = self._append_context(rurl, context)
        rurl = self._append_instanceid(rurl, vmuuid)
        return rurl

    def _append_context(self, rurl, context=None):
        # The context is always optional, to allow incremental exploitation of
        # the new parameter.  When it is present and it has a request ID, xCAT
        # logs the request ID so it's easier to link xCAT log entries to
        # OpenStack log entries.
        if context is not None:
            try:
                rurl = rurl + self.PCONTEXT + context.request_id
            except Exception as err:
                # Cannot use format_message() in this context, because the
                # Exception class does not implement that method.
                msg = _("Failed to append request ID to URL %(url)s : %(err)s"
                        ) % {'url': rurl, 'err': six.text_type(err)}
                LOG.error(msg)
                # Continue and return the original URL once the error is logged
                # Failing the request over this is NOT desired.
        return rurl

    def _append_instanceid(self, rurl, vmuuid):
        # The instance ID is always optional.  When it is present, xCAT logs it
        # so it's easier to link xCAT log entries to OpenStack log entries.
        if vmuuid:
            rurl = rurl + self.PUUID + vmuuid
        return rurl

    def _append_nodeRange(self, rurl, nodeRange):
        if nodeRange:
            rurl = rurl + self.PNODERANGE + nodeRange
        return rurl

    def _hv(self, arg=''):
        return self.PREFIX + self.HV + arg + self.SUFFIX

    def _diag_logs(self, arg='', nodeRange='', vmuuid='', context=None):
        rurl = self.PREFIX + self.DIAGLOGS + arg + self.SUFFIX
        rurl = self._append_nodeRange(rurl, nodeRange)
        rurl = self._append_context(rurl, context)
        rurl = self._append_instanceid(rurl, vmuuid)
        return rurl

    def rpower(self, arg=''):
        return self.PREFIX + self.NODES + arg + self.POWER + self.SUFFIX

    def nodels(self, arg=''):
        return self._nodes(arg)

    def rinv(self, arg='', addp=None):
        rurl = self.PREFIX + self.NODES + arg + self.INVENTORY + self.SUFFIX
        return self._append_addp(rurl, addp)

    def mkdef(self, arg=''):
        return self._nodes(arg)

    def rmdef(self, arg=''):
        return self._nodes(arg)

    def nodestat(self, arg=''):
        return self.PREFIX + self.NODES + arg + self.STATUS + self.SUFFIX

    def chvm(self, arg=''):
        return self._vms(arg)

    def lsvm(self, arg=''):
        return self._vms(arg)

    def chhv(self, arg=''):
        return self._hv(arg)

    def mkvm(self, arg='', vmuuid='', context=None):
        rurl = self._vms(arg, vmuuid, context)
        return rurl

    def rmvm(self, arg='', vmuuid='', context=None):
        rurl = self._vms(arg, vmuuid, context)
        return rurl

    def tabdump(self, arg='', addp=None):
        rurl = self.PREFIX + self.TABLES + arg + self.SUFFIX
        return self._append_addp(rurl, addp)

    def _append_addp(self, rurl, addp=None):
        if addp is not None:
            return rurl + addp
        else:
            return rurl

    def imgcapture(self, arg=''):
        return self.PREFIX + self.IMAGES + arg + self.CAPTURE + self.SUFFIX

    def imgexport(self, arg=''):
        return self.PREFIX + self.IMAGES + arg + self.EXPORT + self.SUFFIX

    def rmimage(self, arg=''):
        return self.PREFIX + self.IMAGES + arg + self.SUFFIX

    def rmobject(self, arg=''):
        return self.PREFIX + self.OBJECTS + arg + self.SUFFIX

    def lsdef_node(self, arg='', addp=None):
        rurl = self.PREFIX + self.NODES + arg + self.SUFFIX
        return self._append_addp(rurl, addp)

    def lsdef_image(self, arg='', addp=None):
        rurl = self.PREFIX + self.IMAGES + arg + self.SUFFIX
        return self._append_addp(rurl, addp)

    def imgimport(self, arg=''):
        return self.PREFIX + self.IMAGES + self.IMGIMPORT + arg + self.SUFFIX

    def chtab(self, arg=''):
        return self.PREFIX + self.NODES + arg + self.SUFFIX

    def nodeset(self, arg=''):
        return self.PREFIX + self.NODES + arg + self.BOOTSTAT + self.SUFFIX

    def rmigrate(self, arg=''):
        return self.PREFIX + self.NODES + arg + self.MIGRATE + self.SUFFIX

    def gettab(self, arg='', addp=None):
        rurl = self.PREFIX + self.TABLES + arg + self.SUFFIX
        return self._append_addp(rurl, addp)

    def tabch(self, arg='', addp=None):
        """Add/update/delete row(s) in table arg, with attribute addp."""
        rurl = self.PREFIX + self.TABLES + arg + self.SUFFIX
        return self._append_addp(rurl, addp)

    def xdsh(self, arg=''):
        """Run shell command."""
        return self.PREFIX + self.NODES + arg + self.XDSH + self.SUFFIX

    def execcmdonvm(self, arg=''):
        """Run shell command after support IUCV."""
        return self.PREFIX + self.NODES + arg + self.EXECCMDONVM + self.SUFFIX

    def network(self, arg='', addp=None):
        rurl = self.PREFIX + self.NETWORK + arg + self.SUFFIX
        if addp is not None:
            return rurl + addp
        else:
            return rurl

    def version(self):
        return self.PREFIX + self.VERSION + self.SUFFIX

    def mkdiag(self, arg='', nodeRange='', vmuuid='', context=None):
        rurl = self._diag_logs(arg, nodeRange, vmuuid, context)
        return rurl


class HTTPSClientAuthConnection(httplib.HTTPSConnection):
    """For https://wiki.openstack.org/wiki/OSSN/OSSN-0033"""

    def __init__(self, host, port, ca_file, timeout=None, key_file=None,
                 cert_file=None):
        httplib.HTTPSConnection.__init__(self, host, port,
                                         key_file=key_file,
                                         cert_file=cert_file)
        self.key_file = key_file
        self.cert_file = cert_file
        self.ca_file = ca_file
        self.timeout = timeout
        self.use_ca = True

        if self.ca_file is None:
            LOG.debug("no xCAT CA file specified, this is considered "
                      "not secure")
            self.use_ca = False

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()

        if (self.ca_file is not None and
            not os.path.exists(self.ca_file)):
            LOG.warning(_LW("the CA file %(ca_file) does not exist!"),
                        {'ca_file': self.ca_file})
            self.use_ca = False

        if not self.use_ca:
            self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file,
                                        cert_reqs=ssl.CERT_NONE)
        else:
            self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file,
                                        ca_certs=self.ca_file,
                                        cert_reqs=ssl.CERT_REQUIRED)


def get_xcat_url():
    global _XCAT_URL

    if _XCAT_URL is not None:
        return _XCAT_URL

    _XCAT_URL = XCATUrl()
    return _XCAT_URL


def remove_prefix_of_unicode(str_unicode):
    str_unicode = str_unicode.encode('unicode_escape')
    str_unicode = str_unicode.replace('\u', '')
    str_unicode = str_unicode.decode('utf-8')
    return str_unicode


class XCATConnection(object):
    """Https requests to xCAT web service."""

    def __init__(self):
        """Initialize https connection to xCAT service."""
        self.port = 443
        self.host = CONF.zvm_xcat_server
        self.conn = HTTPSClientAuthConnection(self.host, self.port,
                        CONF.zvm_xcat_ca_file,
                        timeout=CONF.zvm_xcat_connection_timeout)

    def request(self, method, url, body=None, headers=None):
        """Send https request to xCAT server.

        Will return a python dictionary including:
        {'status': http return code,
         'reason': http reason,
         'message': response message}

        """
        headers = headers or {}
        if body is not None:
            body = jsonutils.dumps(body)
            headers = {'content-type': 'text/plain',
                       'content-length': len(body)}

        _rep_ptn = ''.join(('&password=', CONF.zvm_xcat_password))
        LOG.debug("Sending request to xCAT. xCAT-Server:%(xcat_server)s "
                  "Request-method:%(method)s "
                  "URL:%(url)s "
                  "Headers:%(headers)s "
                  "Body:%(body)s",
                  {'xcat_server': CONF.zvm_xcat_server,
                   'method': method,
                   'url': url.replace(_rep_ptn, ''),  # hide password in log
                   'headers': str(headers),
                   'body': body})

        try:
            self.conn.request(method, url, body, headers)
        except socket.gaierror as err:
            msg = _("Failed to find address: %s") % err
            raise exception.ZVMXCATRequestFailed(xcatserver=self.host, msg=msg)
        except (socket.error, socket.timeout) as err:
            msg = _("Communication error: %s") % err
            raise exception.ZVMXCATRequestFailed(xcatserver=self.host, msg=msg)

        try:
            res = self.conn.getresponse()
        except Exception as err:
            msg = _("Failed to get response from xCAT: %s") % err
            raise exception.ZVMXCATRequestFailed(xcatserver=self.host, msg=msg)

        msg = res.read()
        resp = {
            'status': res.status,
            'reason': res.reason,
            'message': msg}

        LOG.debug("xCAT response: %s", str(resp))

        # Only "200" or "201" returned from xCAT can be considered
        # as good status.
        err = None
        need_retry = 0

        # 503 is special handle here, we need change it and logs below
        # if additional retry condition need to be added.
        if res.status != 503:
            if method == "POST":
                if res.status != 201:
                    err = str(resp)
            else:
                if res.status != 200:
                    err = str(resp)
        else:
            need_retry = 1

        if err is not None:
            raise exception.ZVMXCATRequestFailed(xcatserver=self.host,
                                                 msg=err)

        return need_retry, resp


def xcat_request(method, url, body=None, headers=None, ignore_warning=False):
    headers = headers or {}
    conn = XCATConnection()

    # Maximum allow 5 retry for service unavailable
    _rep_ptn = ''.join(('&password=', CONF.zvm_xcat_password))

    max_attempts = 5
    retry_attempts = max_attempts

    while (retry_attempts > 0):
        need_retry, resp = conn.request(method, url, body, headers)
        if not need_retry:
            ret = load_xcat_resp(resp['message'],
                                 ignore_warning=ignore_warning)
            # Yes, we finished the request, let's return or handle error
            return ret

        LOG.info(_LI("xCAT encounter service handling error (http 503), "
                     "Attempt %(retry)s of %(max_retry)s "
                     "request: xCAT-Server: %(xcat_server)s "
                     "Request-method: %(method)s "
                     "URL: %(url)s."),
                 {'retry': max_attempts - retry_attempts + 1,
                  'max_retry': max_attempts,
                  'xcat_server': CONF.zvm_xcat_server,
                  'method': method,
                  'url': url.replace(_rep_ptn, '')})

        retry_attempts -= 1
        if retry_attempts > 0:
            time.sleep(2)

    LOG.warning(_LW("xCAT encounter service handling error (http 503), "
                    "Retried %(max_retry)s times but still failed. "
                    "request: xCAT-Server: %(xcat_server)s "
                    "Request-method: %(method)s "
                    "URL: %(url)s."),
                {'max_retry': max_attempts,
                 'xcat_server': CONF.zvm_xcat_server,
                 'method': method,
                 'url': url.replace(_rep_ptn, '')})
    ret = load_xcat_resp(resp['message'],
                         ignore_warning=ignore_warning)

    # ok, we tried all we can do here, let upper layer handle this error.
    return ret


def jsonloads(jsonstr):
    try:
        return jsonutils.loads(jsonstr)
    except ValueError:
        errmsg = _("xCAT response data is not in JSON format")
        LOG.error(errmsg)
        raise exception.ZVMDriverError(msg=errmsg)


@contextlib.contextmanager
def expect_invalid_xcat_resp_data(data=''):
    """Catch exceptions when using xCAT response data."""
    try:
        yield
    except (ValueError, TypeError, IndexError, AttributeError,
            KeyError) as err:
        LOG.error(_('Parse %s encounter error'), data)
        raise exception.ZVMInvalidXCATResponseDataError(msg=err)


def wrap_invalid_xcat_resp_data_error(function):
    """Catch exceptions when using xCAT response data."""

    @functools.wraps(function)
    def decorated_function(*arg, **kwargs):
        try:
            return function(*arg, **kwargs)
        except (ValueError, TypeError, IndexError, AttributeError,
                KeyError) as err:
            raise exception.ZVMInvalidXCATResponseDataError(msg=err)

    return decorated_function


@contextlib.contextmanager
def ignore_errors():
    """Only execute the clauses and ignore the results."""

    try:
        yield
    except Exception as err:
        emsg = format_exception_msg(err)
        LOG.debug("Ignore an error: %s", emsg)
        pass


@contextlib.contextmanager
def except_xcat_call_failed_and_reraise(exc, **kwargs):
    """Catch all kinds of xCAT call failure and reraise.

    exc: the exception that would be raised.
    """
    try:
        yield
    except (exception.ZVMXCATRequestFailed,
            exception.ZVMInvalidXCATResponseDataError,
            exception.ZVMXCATInternalError) as err:
        msg = err.format_message()
        kwargs['msg'] = msg
        LOG.error(_LE('XCAT response return error: %s'), msg)
        raise exc(**kwargs)


def convert_to_mb(s):
    """Convert memory size from GB to MB."""
    s = s.upper()
    try:
        if s.endswith('G'):
            return float(s[:-1].strip()) * 1024
        elif s.endswith('T'):
            return float(s[:-1].strip()) * 1024 * 1024
        else:
            return float(s[:-1].strip())
    except (IndexError, ValueError, KeyError, TypeError) as e:
        errmsg = _("Invalid memory format: %s") % e
        raise exception.ZVMDriverError(msg=errmsg)


@wrap_invalid_xcat_resp_data_error
def translate_xcat_resp(rawdata, dirt):
    """Translate xCAT response JSON stream to a python dictionary.

    xCAT response example:
    node: keyword1: value1\n
    node: keyword2: value2\n
    ...
    node: keywordn: valuen\n

    Will return a python dictionary:
    {keyword1: value1,
     keyword2: value2,
     ...
     keywordn: valuen,}

    """
    data_list = rawdata.split("\n")

    data = {}

    for ls in data_list:
        for k in list(dirt.keys()):
            if ls.__contains__(dirt[k]):
                data[k] = ls[(ls.find(dirt[k]) + len(dirt[k])):].strip()
                break

    if data == {}:
        msg = _("No value matched with keywords. Raw Data: %(raw)s; "
                "Keywords: %(kws)s") % {'raw': rawdata, 'kws': str(dirt)}
        raise exception.ZVMInvalidXCATResponseDataError(msg=msg)

    return data


def mapping_power_stat(power_stat):
    """Translate power state to OpenStack defined constants."""
    return const.ZVM_POWER_STAT.get(power_stat, power_state.NOSTATE)


@wrap_invalid_xcat_resp_data_error
def load_xcat_resp(message, ignore_warning=False):
    """Abstract information from xCAT REST response body.

    As default, xCAT response will in format of JSON and can be
    converted to Python dictionary, would looks like:
    {"data": [{"info": [info,]}, {"data": [data,]}, ..., {"error": [error,]}]}

    Returns a Python dictionary, looks like:
    {'info': [info,],
     'data': [data,],
     ...
     'error': [error,]}

    """
    resp_list = jsonloads(message)['data']
    keys = const.XCAT_RESPONSE_KEYS

    resp = {}

    for k in keys:
        resp[k] = []

    for d in resp_list:
        for k in keys:
            if d.get(k) is not None:
                resp[k].append(d.get(k))

    err = resp.get('error')
    if err != []:
        for e in err:
            if _is_warning_or_recoverable_issue(str(e)):
                # ignore known warnings or errors:
                continue
            else:
                raise exception.ZVMXCATInternalError(msg=message)

    if not ignore_warning:
        _log_warnings(resp)

    return resp


def _log_warnings(resp):
    for msg in (resp['info'], resp['node'], resp['data']):
        msgstr = str(msg)
        if 'warn' in msgstr.lower():
            LOG.info(_LI("Warning from xCAT: %s"), msgstr)


def _is_warning_or_recoverable_issue(err_str):
    return _is_warning(err_str) or _is_recoverable_issue(err_str)


def _is_recoverable_issue(err_str):
    dirmaint_request_counter_save = ['Return Code: 596', 'Reason Code: 1185']
    dirmaint_request_limit = ['Return Code: 596', 'Reason Code: 6312']
    recoverable_issues = [dirmaint_request_counter_save,
                          dirmaint_request_limit]
    for issue in recoverable_issues:
        # Search all matchs in the return value
        # any mismatch leads to recoverable not empty
        recoverable = [t for t in issue if t not in err_str]
        if recoverable == []:
            return True

    return False


def _is_warning(err_str):
    ignore_list = (
        'Warning: the RSA host key for',
        'Warning: Permanently added',
        'WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED',
    )

    for im in ignore_list:
        if im in err_str:
            return True

    return False


def _volume_in_mapping(mount_device, block_device_info):
    block_device_list = [block_device.strip_dev(vol['mount_device'])
                         for vol in
                         driver.block_device_info_get_mapping(
                             block_device_info)]
    LOG.debug("block_device_list %s", block_device_list)
    return block_device.strip_dev(mount_device) in block_device_list


def is_volume_root(root_device, mountpoint):
    """This judges if the moutpoint equals the root_device."""
    return block_device.strip_dev(mountpoint) == block_device.strip_dev(
                                                                root_device)


def is_boot_from_volume(block_device_info):
    root_mount_device = driver.block_device_info_get_root(block_device_info)
    boot_from_volume = _volume_in_mapping(root_mount_device,
                                          block_device_info)
    return root_mount_device, boot_from_volume


def get_host():
    return ''.join([pwd.getpwuid(os.geteuid()).pw_name, '@', CONF.my_ip])


def get_xcat_version():
    """Return the version of xCAT"""
    url = get_xcat_url().version()
    data = xcat_request('GET', url)['data']

    with expect_invalid_xcat_resp_data(data):
        version = data[0][0].split()[1]
        version = version.strip()
        return version


def xcat_support_chvm_smcli():
    """Return true if xCAT version support clone"""
    xcat_version = get_xcat_version()
    return map(int, xcat_version.split('.')) >= map(int,
        const.XCAT_SUPPORT_CHVM_SMCLI_VERSION.split('.'))


def xcat_support_mkvm_ipl_param(xcat_version):
    return map(int, xcat_version.split('.')) >= map(int,
        const.XCAT_MKVM_SUPPORT_IPL.split('.'))


def xcat_support_deployment_failure_diagnostics(xcat_version):
    """Return true if xCAT version supports deployment failure diagnostics"""
    return map(int, xcat_version.split('.')) >= map(int,
        const.XCAT_SUPPORT_COLLECT_DIAGNOSTICS_DEPLOYFAILED.split('.'))


def xcat_support_iucv(xcat_version=None):
    if xcat_version is None:
        xcat_version = get_xcat_version()
    return map(int, xcat_version.split('.')) >= map(int,
        const.XCAT_SUPPORT_IUCV.split('.'))


def get_userid(node_name):
    """Returns z/VM userid for the xCAT node."""
    url = get_xcat_url().lsdef_node(''.join(['/', node_name]))
    info = xcat_request('GET', url)
    with expect_invalid_xcat_resp_data(info):
        for s in info['info'][0]:
            if s.__contains__('userid='):
                return s.strip().rpartition('=')[2]


def xdsh(node, commands):
    """"Run command on xCAT node."""
    LOG.debug('Run command %(cmd)s on xCAT node %(node)s',
              {'cmd': commands, 'node': node})

    def xdsh_execute(node, commands):
        """Invoke xCAT REST API to execute command on node."""
        xdsh_commands = 'command=%s' % commands
        # Add -q (quiet) option to ignore ssh warnings and banner msg
        opt = 'options=-q'
        body = [xdsh_commands, opt]
        url = get_xcat_url().xdsh('/' + node)
        return xcat_request("PUT", url, body)

    with except_xcat_call_failed_and_reraise(
            exception.ZVMXCATXdshFailed):
        res_dict = xdsh_execute(node, commands)

    return res_dict


def execcmdonvm(node, commands):
    """"Run command on VM."""
    LOG.debug('Run command %(cmd)s on VM %(node)s',
              {'cmd': commands, 'node': node})

    body = [commands]
    url = get_xcat_url().execcmdonvm('/' + node)
    return xcat_request("PUT", url, body)


def punch_file(node, fn, fclass, remote_host=None, del_src=True):
    """punch file to reader. """
    if remote_host:
        body = [" ".join(['--punchfile', fn, fclass, remote_host])]
    else:
        body = [" ".join(['--punchfile', fn, fclass])]
    url = get_xcat_url().chvm('/' + node)

    try:
        xcat_request("PUT", url, body)
    except Exception as err:
        emsg = format_exception_msg(err)
        with excutils.save_and_reraise_exception():
            LOG.error(_('Punch file to %(node)s failed: %(msg)s'),
                      {'node': node, 'msg': emsg})
    finally:
        if del_src:
            os.remove(fn)


def punch_adminpass_file(instance_path, instance_name, admin_password,
                         linuxdist):
    adminpass_fn = ''.join([instance_path, '/adminpwd.sh'])
    _generate_adminpass_file(adminpass_fn, admin_password, linuxdist)
    punch_file(instance_name, adminpass_fn, 'X', remote_host=get_host())


def _generate_iucv_cmd_file(iucv_cmd_file_path, cmd):
    lines = ['#!/bin/bash\n', cmd]
    with open(iucv_cmd_file_path, 'w') as f:
        f.writelines(lines)


def add_iucv_in_zvm_table(instance_name):
    result = xcat_cmd_gettab('zvm', 'node', instance_name, "status")
    if not result:
        status = "IUCV=1"
    else:
        status = result + ';IUCV=1'
    xcat_cmd_settab('zvm', 'node', instance_name, "status", status)


def copy_zvm_table_status(des_inst_name, src_inst_name):
    status = xcat_cmd_gettab('zvm', 'node', src_inst_name, "status")
    xcat_cmd_settab('zvm', 'node', des_inst_name, "status", status)


def punch_iucv_file(os_ver, zhcp, zhcp_userid, instance_name,
                    instance_path):
    """put iucv server and service files to reader."""
    xcat_iucv_path = '/opt/zhcp/bin/IUCV'
    # generate iucvserver file
    iucv_server_fn = ''.join([xcat_iucv_path, '/iucvserv'])
    iucv_server_path = '/usr/bin/iucvserv'
    punch_path = '/var/opt/xcat/transport'
    # generate iucvserver service and script file
    iucv_service_path = ''
    start_iucv_service_sh = ''
    (distro, release) = dist.ListDistManager().parse_dist(os_ver)
    if((distro == "rhel" and release < '7') or (
                 distro == "sles" and release < '12')):
        iucv_serverd_fn = ''.join([xcat_iucv_path, '/iucvserd'])
        iucv_service_name = 'iucvserd'
        iucv_service_path = '/etc/init.d/' + iucv_service_name
        start_iucv_service_sh = 'chkconfig --add iucvserd 2>&1\n'
        start_iucv_service_sh += 'service iucvserd  start 2>&1\n'
    else:
        iucv_serverd_fn = ''.join([xcat_iucv_path, '/iucvserd.service'])
        iucv_service_name = 'iucvserd.service'
        start_iucv_service_sh = 'systemctl enable iucvserd 2>&1\n'
        start_iucv_service_sh += 'systemctl start iucvserd 2>&1\n'
        if((distro == "rhel" and release >= '7') or (distro == "ubuntu")):
            iucv_service_path = '/lib/systemd/system/' + iucv_service_name
        if(distro == "sles" and release >= '12'):
            iucv_service_path = '/usr/lib/systemd/system/' + iucv_service_name

    iucv_cmd_file_path = instance_path + '/iucvexec.sh'
    cmd = '\n'.join((
        # if when execute this script, conf4z hasn't received iucv file.
        "spoolid=`vmur li | awk '/iucvserv/{print \$2}'|tail -1`",
        "vmur re -f $spoolid /usr/bin/iucvserv 2>&1 >/var/log/messages",
        "spoolid=`vmur li | awk '/iucvserd/{print \$2}'|tail -1`",
        "vmur re -f $spoolid %s  2>&1 >/var/log/messages" % iucv_service_path,
        # if conf4z has received iucv files first.
        "cp -rf %s/iucvserv %s 2>&1 >/var/log/messages" % (punch_path,
                                                           iucv_server_path),
        "cp -rf %s/%s %s 2>&1 >/var/log/messages" % (punch_path,
                                        iucv_service_name, iucv_service_path),
        "chmod +x %s %s" % (iucv_server_path, iucv_service_path),
        "echo -n %s >/etc/iucv_authorized_userid 2>&1" % zhcp_userid,
        start_iucv_service_sh
        ))
    _generate_iucv_cmd_file(iucv_cmd_file_path, cmd)
    punch_file(instance_name, iucv_server_fn, 'X',
                                   remote_host=zhcp, del_src=False)
    punch_file(instance_name, iucv_serverd_fn, 'X',
                                   remote_host=zhcp, del_src=False)
    punch_file(instance_name, iucv_cmd_file_path, 'X', remote_host=get_host(),
                                                     del_src=False)
    # set VM's communicate type is IUCV
    add_iucv_in_zvm_table(instance_name)


def punch_iucv_authorized_file(old_inst_name, new_inst_name, zhcp_userid):
    cmd = "echo -n %s >/etc/iucv_authorized_userid 2>&1" % zhcp_userid
    iucv_cmd_file_path = '/tmp/%s.sh' % new_inst_name[-8:]  # nosec
    _generate_iucv_cmd_file(iucv_cmd_file_path, cmd)
    punch_file(new_inst_name, iucv_cmd_file_path, 'X', remote_host=get_host())
    if old_inst_name != new_inst_name:
        # set VM's communicate type the same as original VM
        copy_zvm_table_status(old_inst_name, new_inst_name)


def process_eph_disk(instance_name, vdev=None, fmt=None, mntdir=None):
    if not fmt:
        fmt = CONF.default_ephemeral_format or const.DEFAULT_EPH_DISK_FMT
    vdev = vdev or CONF.zvm_user_adde_vdev
    mntdir = mntdir or CONF.zvm_default_ephemeral_mntdir

    eph_parms = _generate_eph_parmline(vdev, fmt, mntdir)
    aemod_handler(instance_name, const.DISK_FUNC_NAME, eph_parms)


def process_swap_disk(instance_name, vdev):
    swap_parms = _generate_swap_parmline(vdev)
    aemod_handler(instance_name, const.DISK_FUNC_NAME, swap_parms)


def aemod_handler(instance_name, func_name, parms):
    url = get_xcat_url().chvm('/' + instance_name)
    body = [" ".join(['--aemod', func_name, parms])]

    try:
        xcat_request("PUT", url, body)
    except Exception as err:
        emsg = format_exception_msg(err)
        with excutils.save_and_reraise_exception():
            LOG.error(_LE('Invoke AE method function: %(func)s on %(node)s '
                          'failed with reason: %(msg)s'),
                     {'func': func_name, 'node': instance_name, 'msg': emsg})


def punch_configdrive_file(transportfiles, instance_name):
    punch_file(instance_name, transportfiles, 'X', remote_host=get_host())


def punch_zipl_file(instance_path, instance_name, lun, wwpn, fcp, volume_meta):
    zipl_fn = ''.join([instance_path, '/ziplset.sh'])
    _generate_zipl_file(zipl_fn, lun, wwpn, fcp, volume_meta)
    punch_file(instance_name, zipl_fn, 'X', remote_host=get_host())


def generate_vdev(base, offset=1):
    """Generate virtual device number base on base vdev.

    :param base: base virtual device number, string of 4 bit hex.
    :param offset: offset to base, integer.

    :output: virtual device number, string of 4 bit hex.
    """
    vdev = hex(int(base, 16) + offset)[2:]
    return vdev.rjust(4, '0')


def generate_disk_vdev(offset=1):
    """Generate virtual device number for ephemeral and swap disks.

    :parm offset: offset to zvm_user_adde_vdev.

    :output: virtual device number, string of 4 bit hex.
    """
    vdev = generate_vdev(CONF.zvm_user_adde_vdev, offset + 1)
    if offset >= 0 and offset < 254:
        return vdev
    else:
        msg = _("Invalid virtual device number for disk: %s") % vdev
        LOG.error(msg)
        raise exception.ZVMDriverError(msg=msg)


def _generate_eph_parmline(vdev, fmt, mntdir):

    parms = [
        'action=addMdisk',
        'vaddr=' + vdev,
        'filesys=' + fmt,
        'mntdir=' + mntdir
        ]
    parmline = ' '.join(parms)
    return parmline


def _generate_swap_parmline(vdev):

    parms = [
        'action=addSwap',
        'vaddr=' + vdev,
        ]
    parmline = ' '.join(parms)
    return parmline


def _generate_auth_file(fn, pub_key):
    lines = ['#!/bin/bash\n',
    'echo "%s" >> /root/.ssh/authorized_keys' % pub_key]
    with open(fn, 'w') as f:
        f.writelines(lines)


def _generate_adminpass_file(fn, admin_password, linuxdist):
    pwd_str = linuxdist.get_change_passwd_command(admin_password)
    lines = ['#!/bin/bash\n', pwd_str]
    with open(fn, 'w') as f:
        f.writelines(lines)


def _generate_zipl_file(fn, lun, wwpn, fcp, volume_meta):
    image = volume_meta['image']
    ramdisk = volume_meta['ramdisk']
    root = volume_meta['root']
    os_version = volume_meta['os_version']
    dist_manager = dist.ListDistManager()
    linux_dist = dist_manager.get_linux_dist(os_version)()
    zipl_script_lines = linux_dist.get_zipl_script_lines(
                            image, ramdisk, root, fcp, wwpn, lun)
    with open(fn, 'w') as f:
        f.writelines(zipl_script_lines)


@wrap_invalid_xcat_resp_data_error
def get_mn_pub_key():
    cmd = 'cat /root/.ssh/id_rsa.pub'
    resp = xdsh(CONF.zvm_xcat_master, cmd)
    key = resp['data'][0][0]
    start_idx = key.find('ssh-rsa')
    key = key[start_idx:]
    return key


def parse_os_version(os_version):
    """Separate os and version from os_version.
    Possible return value are only:
    ('rhel', x.y) and ('sles', x.y) where x.y may not be digits
    """
    supported = {'rhel': ['rhel', 'redhat', 'red hat'],
                'sles': ['suse', 'sles']}
    os_version = os_version.lower()
    for distro, patterns in supported.items():
        for i in patterns:
            if os_version.startswith(i):
                # Not guarrentee the version is digital
                return distro, os_version.split(i, 2)[1]
    else:
        raise exception.ZVMImageError(msg='Unknown os_version property')


def xcat_cmd_gettab(table, col, col_value, attr):
    addp = ("&col=%(col)s=%(col_value)s&attribute=%(attr)s" %
            {'col': col, 'col_value': col_value, 'attr': attr})
    url = get_xcat_url().gettab('/%s' % table, addp)
    res_info = xcat_request("GET", url)
    with expect_invalid_xcat_resp_data(res_info):
        if res_info['data']:
            return res_info['data'][0][0]
        else:
            return ''


def xcat_cmd_settab(table, col, col_value, attr, value):
    """Add status value for node in database table."""
    commands = ("%(col)s=%(col_value)s %(table)s.%(attr)s=%(value)s" %
              {'col': col, 'col_value': col_value,
                         'attr': attr, 'table': table, 'value': value})
    url = get_xcat_url().tabch("/%s" % table)
    body = [commands]
    with expect_invalid_xcat_resp_data():
        return xcat_request("PUT", url, body)['data']


def xcat_cmd_gettab_multi_attr(table, col, col_value, attr_list):
    attr_str = ''.join(["&attribute=%s" % attr for attr in attr_list])
    addp = ("&col=%(col)s=%(col_value)s&%(attr)s" %
            {'col': col, 'col_value': col_value, 'attr': attr_str})
    url = get_xcat_url().gettab('/%s' % table, addp)
    res_data = xcat_request("GET", url)['data']

    outp = {}
    with expect_invalid_xcat_resp_data(res_data):
        for attr in attr_list:
            for data in res_data:
                if attr in data[0]:
                    outp[attr] = data[0].rpartition(':')[2].strip()
                    res_data.remove(data)
                    break

    return outp


def format_exception_msg(exc_obj):
    """Return message string from nova exceptions and common exceptions."""
    if isinstance(exc_obj, nova_exception.NovaException):
        return exc_obj.format_message()
    else:
        return str(exc_obj)


def looping_call(f, sleep=5, inc_sleep=0, max_sleep=60, timeout=600,
                 exceptions=(), *args, **kwargs):
    """Helper function that to run looping call with fixed/dynamical interval.

    :param f:          the looping call function or method.
    :param sleep:      initial interval of the looping calls.
    :param inc_sleep:  sleep time increment, default as 0.
    :param max_sleep:  max sleep time.
    :param timeout:    looping call timeout in seconds, 0 means no timeout.
    :param exceptions: exceptions that trigger re-try.

    """
    time_start = time.time()
    expiration = time_start + timeout

    retry = True
    while retry:
        expired = timeout and (time.time() > expiration)

        try:
            f(*args, **kwargs)
        except exceptions:
            retry = not expired
            if retry:
                LOG.debug("Will re-try %(fname)s in %(itv)d seconds",
                          {'fname': f.__name__, 'itv': sleep})
                time.sleep(sleep)
                sleep = min(sleep + inc_sleep, max_sleep)
            else:
                LOG.debug("Looping call %s timeout", f.__name__)
            continue
        retry = False


class PathUtils(object):
    def open(self, path, mode):
        """Wrapper on six.moves.builtins.open used to simplify unit testing."""
        return six.moves.builtins.open(path, mode)

    def _get_image_tmp_path(self):
        image_tmp_path = os.path.normpath(CONF.zvm_image_tmp_path)
        if not os.path.exists(image_tmp_path):
            LOG.debug('Creating folder %s for image temp files',
                     image_tmp_path)
            os.makedirs(image_tmp_path)
        return image_tmp_path

    def get_bundle_tmp_path(self, tmp_file_fn):
        bundle_tmp_path = os.path.join(self._get_image_tmp_path(), "spawn_tmp",
                                       tmp_file_fn)
        if not os.path.exists(bundle_tmp_path):
            LOG.debug('Creating folder %s for image bundle temp file',
                      bundle_tmp_path)
            os.makedirs(bundle_tmp_path)
        return bundle_tmp_path

    def get_img_path(self, bundle_file_path, image_name):
        return os.path.join(bundle_file_path, image_name)

    def _get_snapshot_path(self):
        snapshot_folder = os.path.join(self._get_image_tmp_path(),
                                       "snapshot_tmp")
        if not os.path.exists(snapshot_folder):
            LOG.debug("Creating the snapshot folder %s", snapshot_folder)
            os.makedirs(snapshot_folder)
        return snapshot_folder

    def _get_punch_path(self):
        punch_folder = os.path.join(self._get_image_tmp_path(), "punch_tmp")
        if not os.path.exists(punch_folder):
            LOG.debug("Creating the punch folder %s", punch_folder)
            os.makedirs(punch_folder)
        return punch_folder

    def get_spawn_folder(self):
        spawn_folder = os.path.join(self._get_image_tmp_path(), "spawn_tmp")
        if not os.path.exists(spawn_folder):
            LOG.debug("Creating the spawn folder %s", spawn_folder)
            os.makedirs(spawn_folder)
        return spawn_folder

    def make_time_stamp(self):
        tmp_file_fn = time.strftime('%Y%m%d%H%M%S',
                                            time.localtime(time.time()))
        return tmp_file_fn

    def get_snapshot_time_path(self):
        snapshot_time_path = os.path.join(self._get_snapshot_path(),
                                          self.make_time_stamp())
        if not os.path.exists(snapshot_time_path):
            LOG.debug('Creating folder %s for image bundle temp file',
                      snapshot_time_path)
            os.makedirs(snapshot_time_path)
        return snapshot_time_path

    def clean_temp_folder(self, tmp_file_fn):
        if os.path.isdir(tmp_file_fn):
            LOG.debug('Removing existing folder %s ', tmp_file_fn)
            shutil.rmtree(tmp_file_fn)

    def _get_instances_path(self):
        return os.path.normpath(CONF.instances_path)

    def get_instance_path(self, os_node, instance_name):
        instance_folder = os.path.join(self._get_instances_path(), os_node,
                                       instance_name)
        if not os.path.exists(instance_folder):
            LOG.debug("Creating the instance path %s", instance_folder)
            os.makedirs(instance_folder)
        return instance_folder

    def get_console_log_path(self, os_node, instance_name):
        return os.path.join(self.get_instance_path(os_node, instance_name),
                            "console.log")


class NetworkUtils(object):
    """Utilities for z/VM network operator."""

    def validate_ip_address(self, ip_address):
        """Check whether ip_address is valid."""
        # TODO(Leon): check IP address format
        pass

    def validate_network_mask(self, mask):
        """Check whether mask is valid."""
        # TODO(Leon): check network mask format
        pass

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
        cfg_str = ''
        # Red Hat
        file_path_rhel = '/etc/sysconfig/network-scripts/'
        # SuSE
        file_path_sles = '/etc/sysconfig/network/'
        file_name_route = file_path_sles + 'routes'

        # Check the OS type
        if (os_type == 'sles'):
            file_path = file_path_sles
        else:
            file_path = file_path_rhel
        file_name_dns = '/etc/resolv.conf'
        for vif in network_info:
            file_name = 'ifcfg-eth' + str(device_num)
            network = vif['network']
            (cfg_str, cmd_str, dns_str,
                route_str) = self.generate_network_configration(network,
                                                base_vdev, device_num, os_type)
            LOG.debug('Network configure file content is: %s', cfg_str)
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
        if os_type == 'sles':
            udev_file_name = '/etc/udev/rules.d/70-persistent-net.rules'
            cfg_files.append((udev_file_name, udev_cfg_str))
            if len(route_cfg_str) > 0:
                cfg_files.append((file_name_route, route_cfg_str))

        return cfg_files, cmd_strings

    def generate_network_configration(self, network, vdev, device_num,
                                      os_type):
        """Generate network configuration items."""
        ip_v4 = netmask_v4 = gateway_v4 = broadcast_v4 = ''
        subchannels = None
        device = None
        cidr_v4 = None
        cmd_str = None
        dns_str = ''
        route_str = ''

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

        cfg_str = 'DEVICE=\"' + device + '\"\n' + 'BOOTPROTO=\"static\"\n'
        cfg_str += 'BROADCAST=\"' + broadcast_v4 + '\"\n'
        cfg_str += 'GATEWAY=\"' + gateway_v4 + '\"\nIPADDR=\"' + ip_v4 + '\"\n'
        cfg_str += 'NETMASK=\"' + netmask_v4 + '\"\n'
        cfg_str += 'NETTYPE=\"qeth\"\nONBOOT=\"yes\"\n'
        cfg_str += 'PORTNAME=\"PORT' + address_read + '\"\n'
        cfg_str += 'OPTIONS=\"layer2=1\"\n'
        cfg_str += 'SUBCHANNELS=\"' + subchannels + '\"\n'

        if os_type == 'sles':
            cidr_v4 = self._get_cidr_from_ip_netmask(ip_v4, netmask_v4)
            cmd_str = 'qeth_configure -l 0.0.%s ' % address_read.lower()
            cmd_str += '0.0.%(write)s 0.0.%(data)s 1\n' % {'write':
                           address_write.lower(), 'data': address_data.lower()}
            cfg_str = "BOOTPROTO=\'static\'\nIPADDR=\'%s\'\n" % cidr_v4
            cfg_str += "BROADCAST=\'%s\'\n" % broadcast_v4
            cfg_str += "STARTMODE=\'onboot\'\n"
            cfg_str += ("NAME=\'OSA Express Network card (%s)\'\n" %
                        address_read)
            route_str += 'default %s - -\n' % gateway_v4

        return cfg_str, cmd_str, dns_str, route_str

    def generate_udev_configuration(self, device, dev_channel):
        cfg_str = 'SUBSYSTEM==\"net\", ACTION==\"add\", DRIVERS==\"qeth\",'
        cfg_str += ' KERNELS==\"%s\", ATTR{type}==\"1\",' % dev_channel
        cfg_str += ' KERNEL==\"eth*\", NAME=\"eth%s\"\n' % device

        return cfg_str

    def _get_cidr_from_ip_netmask(self, ip, netmask):
        netmask_fields = netmask.split('.')
        bin_str = ''
        for octet in netmask_fields:
            bin_str += bin(int(octet))[2:].zfill(8)
        mask = str(len(bin_str.rstrip('0')))
        cidr_v4 = ip + '/' + mask
        return cidr_v4
