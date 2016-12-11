#    Copyright 2015 Red Hat, Inc.
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

from nova import objects
from nova.objects import base as obj_base
from nova.objects import fields


@obj_base.NovaObjectRegistry.register
class ZVMLiveMigrateData(objects.migrate_data.LiveMigrateData):
    # Version 1.0: Initial version
    VERSION = '1.0'

    fields = {
        'dest_host': fields.StringField(),
        'source_xcat_mn': fields.StringField(),
        'zvm_userid': fields.StringField(),
    }

    def obj_make_compatible(self, primitive, target_version):
        super(ZVMLiveMigrateData, self).obj_make_compatible(
            primitive, target_version)

    def to_legacy_dict(self, pre_migration_result=False):
        legacy = super(ZVMLiveMigrateData, self).to_legacy_dict()
        for field in self.fields:
            if self.obj_attr_is_set(field):
                legacy[field] = getattr(self, field)
        return legacy

    def from_legacy_dict(self, legacy):
        super(ZVMLiveMigrateData, self).from_legacy_dict(legacy)
        for field in self.fields:
            if field in legacy:
                setattr(self, field, legacy[field])
