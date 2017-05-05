.. _imageguide:

===========
Image guide
===========

This guideline will describe the requirements, steps to make images, configurations
about the image that can be used in z/VM solution.

Image Requirements
------------------

* supported Linux distribution (for deploy). The following are supported:
  RHEL 6.2, 6.3, 6.4, 6.5, 6.6, and 6.7
  RHEL 7.0, 7.1 and 7.2
  SLES 11.2, 11.3, and 11.4
  SLES 12 and SLES 12.1
  Ubuntu 16.04

* A supported root disk type for snapshot/spawn. The following are supported:
  FBA
  ECKD

* Images created with the previous version of the OpenStack support should be recaptured with an
  updated xcatconf4z installed, version 2.0 or later. see below chapter for more detail on
  xcatconf4z.

* An image deployed on a compute node must match the disk type supported by that compute node, as
  configured by the zvm_diskpool_type property in the nova.conf configuration file. A compute node
  supports deployment on either an ECKD or FBA image, but not both at the same time. If you wish to
  switch image types, you need to change the zvm_diskpool_type and zvm_diskpool properties in the
  nova.conf file, accordingly. Then restart the nova-compute service to make the changes take effect.
 
* If you deploy an instance with an ephemeral disk, both the root disk and the ephemeral disk will be
  created with the disk type that was specified by zvm_diskpool_type property in the nova.conf file. That
  property can specify either ECKD or FBA.

* When resizing, remember that you can only resize an instance to the same type of disk. For example, if
  an instance is built on an FBA type disk, you can resize it to a larger FBA disk, but not to an ECKD
  disk.

* For glance image-create, it is strongly suggested that you capture an instance with a root disk size no
  greater than 5GB. If you really want to capture a larger root device, you will need to logon xCAT MN
  and modify the timeout value in for httpd service to make image-create work as expected.

* For nova boot, it is recommended that you deploy an instance with a root disk size no greater than
  5GB. If you really want to deploy a larger root device, you will need to logon xCAT MN and modify
  the timeout value in for httpd service to make boot work as expected.

* For nova resize operation, we suggest that you resize an instance with a root disk size no greater than
  5GB.

* The network interfaces must be IPv4 interfaces.

* Image names should be restricted to the UTF-8 subset, which corresponds to the ASCII character set. In
  addition, special characters such as /, \, $, %, @ should not be used. For the FBA disk type "vm",
  capture and deploy is supported only for an FBA disk with a single partition. Capture and deploy is
  not supported for the FBA disk type "vm" on a CMS formatted FBA disk.

* The virtual server/Linux instance used as the source of the new image should meet the following criteria:
  1. The root filesystem must not be on a logical volume.
  2. The minidisk on which the root filesystem resides should be a minidisk of the same type as 
     desired for a subsequent deploy (for example, an ECKD disk image should be captured 
     for a subsequent deploy to an ECKD disk),
  3. not be a full-pack minidisk, since cylinder 0 on full-pack minidisks is reserved, and be
     defined with virtual address 0100.
  4. The root disk should have a single partition.
  5. The image being captured should support SSH access using keys instead of specifying a password. The
     subsequent steps to capture the image will perform a key exchange to allow xCAT to access the server.
  6. The image being captured should not have any network interface cards (NICs) defined below virtual
     address 1100.

In addition to the specified criteria, the following recommendations allow for efficient use of the image:

* The minidisk on which the root filesystem resides should be defined as a multiple of full gigabytes in
size (for example, 1GB or 2GB). OpenStack specifies disk sizes in full gigabyte values, whereas z/VM
handles disk sizes in other ways (cylinders for ECKD disks, blocks for FBA disks, and so on). See the
appropriate online information if you need to convert cylinders or blocks to gigabytes; for example:
http://www.mvsforums.com/helpboards/viewtopic.php?t=8316.

* During subsequent deploys of the image, the OpenStack code will ensure that a disk image is not
copied to a disk smaller than the source disk, as this would result in loss of data. The disk specified in
the flavor should therefore be equal to or slightly larger than the source virtual machine's root disk.
IBM recommends specifying the disk size as 0 in the flavor, which will cause the virtual machine to be
created with the same disk size as the source disk.

z/VM required image properties
------------------------------

In addition to common image property, following image properties are needed:

* image_type_xcat:

Fixed value, must be ``linux``

* hypervisor_type:

Fixed value, must be ``zvm``

* architecture:

Fixed value, must be ``s390x``

* os_name:

Fixed value, must be ``Linux``

* os_version:

is the OS version of your capture source node.
Currently, only Red Hat, SUSE, and Ubuntu type images are supported. For a Red Hat type
image, you can specify the OS version as rhelx.y, redhatx.y, or red hatx.y, where x.y is the
release number. For a SUSE type image, you can specify the OS version as slesx.y or susex.y,
where x.y is the release number. For an Ubuntu type image, you can specify the OS version as
ubuntux.y, where x.y is the release number. (If you don't know the real value, you can get it
from the osvers property value in the manifest.xml file.)

* provisioning_method: 

Fixed value, must be ``netboot``

* image_file_name:

Image file name, mostly it's ``0100.img``
