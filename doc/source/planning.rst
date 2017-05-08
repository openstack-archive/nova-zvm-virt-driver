.. _planning:

=========================
Planning and Requirements
=========================

This dicussed the requirements related to the z/VM system, disk storage etc.

z/VM System Requirements
------------------------

* A supported version of z/VM 6.4.

* In order to use live migration, the z/VM system must be configured in a Single System Image (SSI)
  configuration, and must have been created using the IBM-provided installation instructions for SSI
  configurations.

* The appropriate APARs installed, the current list of which can be found at z/VM xCAT Maintenance
  (http://www.vm.ibm.com/sysman/xcmntlvl.html) and z/VM OpenStack Cloud Information
  (http://www.vm.ibm.com/sysman/osmntlvl.html).

.. note::

  IBM z Systems hardware requirements are based on both the applications and the load on the
  system. Please consult your IBM Sales Representative or Business Partner for assistance in determining
  the specific hardware requirements for your environment.
