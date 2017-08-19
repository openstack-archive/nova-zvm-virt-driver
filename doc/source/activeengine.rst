.. _activeengine:

===================
Active Engine Guide
===================

Active engine is used as initial configuration and management tool during deployed machine startup.
Currently z/VM driver use zvmguestconfigure and cloud-init as 2 stage active engine.

Installation and Configuration of zvmguestconfigure
---------------------------------------------------

Cloudlib4zvm supports initiating changes to a Linux on z Systems virtual machine while Linux is shut down or
the virtual machine is logged off. The changes to Linux are implemented using an activation engine (AE)
that is run when Linux is booted the next time. The 1st active engine, zvmguestconfigure, must be installed
in the Linux on z Systems virtual server so it can process change request files transmitted by the cloudlib4sdk
service to the reader of the virtual machine as a class X file.

zvmguestconfigure script should be installed in a machine that can be managed while it is logged off. This
includes a Linux on z Systems that will be captured for netboot or sysclone deploys.

.. note::

   An additional activation engine, cloud-init, should be installed to handle OpenStack related
   tailoring of the system. The cloud-init AE relies on tailoring performed by the zvmguestconfigure.

Installation and Configuration of cloud-init
--------------------------------------------

OpenStack uses cloud-init as its activation engine. Some distributions include cloud-init either already
installed or available to be installed. If your distribution does not include cloud-init, you can download
the code from https://launchpad.net/cloud-init/+download. After installation, if you issue the following
shell command and no errors occur, cloud-init is installed correctly::

    cloud-init init --local

Installation and configuration of cloud-init differs among different Linux distributions, and cloud-init
source code may change. This section provides general information, but you may have to tailor cloud-init
to meet the needs of your Linux distribution. You can find a community-maintained list of dependencies
at http://ibm.biz/cloudinitLoZ.

The z/VM OpenStack support has been tested with cloud-init 0.7.4 and 0.7.5 for RHEL6.x and SLES11.x,
0.7.6 for RHEL7.x and SLES12.x, and 0.7.8 for Ubuntu 16.04. If you are using a different version of
cloud-init, you should change your specification of the indicated commands accordingly.

During cloud-init installation, some dependency packages may be required. You can use zypper and
python setuptools to easily resolve these dependencies. See https://pypi.python.org/pypi/setuptools for
more information.
