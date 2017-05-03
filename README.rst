==========================
openstack/nova-zvm Project
==========================

About this project
------------------

This project provides a Nova virtualization driver for the z/VM hypervisor of
IBM z Systems and IBM LinuxOne machines.

z/VM provides a highly secure and scalable enterprise cloud infrastructure
and an environment for efficiently running multiple diverse critical
applications on IBM z Systems and IBM LinuxONE with support for more
virtual servers than any other platform in a single footprint.
These z/VM virtual servers can run Linux, z/OS and more.
The detailed information can be found at http://www.vm.ibm.com/

The KVM hypervisors on z Systems and LinuxONE machines are supported
by separate Nova virtualization drivers:

* KVM is supported by the standard libvirt/KVM driver in the
  `openstack/nova <http://git.openstack.org/cgit/openstack/nova>`_
  project.

Links
-----

* Documentation: `<http://nova-zvm.readthedocs.io/en/latest/>`_
* Source: `<http://git.openstack.org/cgit/openstack/nova-zvm-virt-driver>`_
* Github shadow: `<https://github.com/openstack/nova-zvm-virt-driver>`_
* Bugs: `<http://bugs.launchpad.net/nova-zvm-virt-driver>`_
* Gerrit: `<https://review.openstack.org/#/q/project:openstack/nova-zvm-virt-driver>`_
* License: Apache 2.0 license
