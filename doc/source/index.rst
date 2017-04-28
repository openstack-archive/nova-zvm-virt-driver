..
      Copyright 2016 IBM
      All Rights Reserved.

      Licensed under the Apache License, Version 2.0 (the "License"); you may
      not use this file except in compliance with the License. You may obtain
      a copy of the License at

          http://www.apache.org/licenses/LICENSE-2.0

      Unless required by applicable law or agreed to in writing, software
      distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
      WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
      License for the specific language governing permissions and limitations
      under the License.

Welcome to nova-zvm's documentation!
====================================

Welcome to nova-zvm driver's documentation!

System z is a family name used by IBM for all of its mainframe computers.
IBM System z are the direct descendants of System/360, announced in 1964,
and the System/370 from 1970s, and now includes the IBM System z9,
the IBM System z10 and the newer IBM zEnterprise. System z is famous for
its high availability and used in government, financial services, retail,
manufacturing, and just about every other industry.

z/VM is a hypervisor for the IBM System z platform that provides a highly
flexible test and production environment. z/VM offers a base for customers
who want to exploit IBM virtualization technology on one of the industry's
best-of- breed server environments, the IBM System z family.

zVM drivers, consist of a set of drivers/plugins for different OpenStack components,
enables OpenStack to communicate with z/VM hypervisor to manage z/VM system and
virtual machines running on the system.

xCAT is an open source scalable distributed computing management and provisioning
tool that provides a unified interface for hardware control, discovery,
and OS diskful/diskfree deployment. For more info, please refer to
http://xcat.org/ and https://github.com/xcat2/xcat-core.

Overview
========

.. toctree::
    :maxdepth: 2

    topology    
    support-matrix

Using the driver
================

.. toctree::
    :maxdepth: 2

    configurations

Creating zVM Images
===================

.. toctree::
    :maxdepth: 2

    images


Contributing to the project
===========================

.. toctree::
    :maxdepth: 2


Links
=====

* Source: `<http://git.openstack.org/cgit/openstack/nova-zvm-virt-driver>`_
* Github shadow: `<https://github.com/openstack/nova-zvm-virt-driver>`_
* Bugs: `<http://bugs.launchpad.net/nova-zvm-virt-driver>`_
* Gerrit: `<https://review.openstack.org/#/q/project:openstack/nova-zvm-virt-driver>`_
