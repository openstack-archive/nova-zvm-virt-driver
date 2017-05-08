.. _topology:

========
Topology
========

Generic concepts and components
-------------------------------

Above picture shows a conceptual view of the relationship between any OpenStack solution and z/VM.

An OpenStack solution is free to run its components wherever it wishes; its options range from running
all components on z/VM, to running some on z/VM and others elsewhere, to running all components on
other platform(s). The solution is also free to source its components wherever it wishes, either using
z/VM. OpenStack enablement components or not.

z/VM ships a set of servers that provide local system management APIs. These servers consist of request
servers that accept local connections, receive the data, and then call one of a set of worker servers to
process the request. These servers are known collectively as SMAPI. The worker servers can interact with
the z/VM hypervisor (CP) or with a directory manager. A directory manager is required for this
environment.

Beginning with z/VM version 6.3, additional functionality is provided by integrated xCAT services. xCAT
is an Open Source scalable distributed computing management and provisioning tool that provides a
unified interface for hardware control, discovery, and deployment, including remote access to the SMAPI
APIs. It can be used for the deployment and administration of Linux servers that OpenStack wants to
manipulate. The z/VM drivers in the OpenStack services communicate with xCAT services via REST
APIs to manage the virtual servers.

xCAT is composed of two main services: the xCAT management node (xCAT MN) and ZHCP. Both the
xCAT MN server and the ZHCP server run within the same virtual machine, called the OPNCLOUD
virtual machine. The xCAT MN coordinates creating, deleting and updating virtual servers. The
management node uses a z/VM hardware control point (ZHCP) to communicate with SMAPI to
implement changes on a z/VM host. Only one instance of the xCAT MN is necessary to support multiple
z/VM hosts. Each z/VM host runs one instance of ZHCP. xCAT MN supports both a GUI for human
interaction and REST APIs for use by programs (for example, OpenStack).

Overall architecture
--------------------

.. image:: ./images/arch.png
