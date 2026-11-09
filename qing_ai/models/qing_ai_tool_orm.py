"""Configured, bounded ORM operations; no model-supplied Python or method names."""

import hashlib
import json
import math
import re
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain


ORM_OPERATIONS = [
    ('search', 'Query Records'), ('read', 'Read Details'), ('count', 'Count Records'),
    ('read_group', 'Grouped Statistics'),
    ('create', 'Create Record'), ('write', 'Update Record'),
    ('archive', 'Archive Record'), ('restore', 'Restore Record'), ('unlink', 'Delete Record'),
]
ORM_ACCESS = {'search': 'read', 'read': 'read', 'count': 'read', 'read_group': 'read', 'create': 'create',
              'write': 'write', 'archive': 'write', 'restore': 'write', 'unlink': 'unlink'}
ORM_WRITE_OPERATIONS = {'create', 'write', 'archive', 'restore', 'unlink'}
FIELD_TYPES = {'char', 'text', 'boolean', 'integer', 'float', 'monetary', 'selection',
               'date', 'datetime', 'many2one'}
ALL_READABLE_FIELDS_HELP = (
    'Automatically include all supported fields readable by the current user, including newly added fields. '
    'Sensitive fields and unsupported field types are excluded. Writable fields remain separately configured.'
)
FILTER_OPERATORS = {'=', '!=', '>', '>=', '<', '<=', 'in', 'not in', 'like', 'ilike'}
FILTER_PATH_DEPTH = 3
SENSITIVE_FIELD = re.compile(r'password|secret|token|api_key|access_key|credential', re.I)
# Chatter, activity, website and display helpers that business questions rarely need; with all
# readable fields enabled they are left out of the planner catalog (validation still accepts them).
CATALOG_SKIP_FIELD = re.compile(
    r'^(?:message_|activity_|my_activity_|website_|rating_|has_message$|properties_|catalog_|show_'
    r'|property_account_|can_image_|suggest_|signup_|routing_|calendar_last_notif|display_name$)'
    r'|_warn_msg$|_sanitized|_api_uid$'
)
# Many2one targets that only carry technical configuration.
CATALOG_SKIP_COMODELS = ('ir.', 'mail.', 'bus.', 'base.', 'payment.', 'auth.')
TECHNICAL_MODELS = ('ir.', 'res.users', 'res.groups', 'res.company', 'res.config.',
                    'qing.ai.', 'mail.', 'bus.', 'base.', 'payment.', 'auth.')
WORKFLOW_MODELS = ('sale.order', 'purchase.order', 'account.', 'stock.', 'mrp.')
PROTECTED_FIELDS = {'id', 'create_uid', 'create_date', 'write_uid', 'write_date',
                    'company_id', 'company_ids', 'state', 'active'}


class QingAiTool(models.Model):
    _inherit = 'qing.ai.tool'

    def _orm_model_name(self):
        """Technical name of the tool model.

        Odoo 20 lets only administrators read ir.model; the name is configuration
        metadata, while access to the business model itself is checked natively.
        """
        self.ensure_one()
        return self.sudo().model_id.model

    def _orm_model(self):
        self.ensure_one()
        name = self._orm_model_name()
        if not name or name not in self.env or name.startswith(TECHNICAL_MODELS):
            raise ValidationError(self.env._('Choose a persistent business model for this tool.'))
        Model = self.env[name].with_context(
            allowed_company_ids=[self.env.company.id], qing_role_company_id=self.env.company.id,
            active_test=self.orm_operation != 'restore',
        )
        if Model._abstract or Model._transient or not Model._auto or not Model._log_access:
            raise ValidationError(self.env._('Choose a persistent business model for this tool.'))
        if self.orm_operation in ORM_WRITE_OPERATIONS and name.startswith(WORKFLOW_MODELS):
            raise ValidationError(self.env._('Use a business template to change workflow documents.'))
        return Model

    def _orm_metadata(self, Model, writing=False):
        # ir.model.fields is administrator-only in Odoo 20. Read configuration
        # metadata elevated, but validate field visibility using the caller's model.
        if not writing and self.orm_all_fields:
            metadata = Model.fields_get(attributes=['string', 'type', 'required', 'selection'])
            return {name: meta for name, meta in metadata.items()
                    if meta['type'] in FIELD_TYPES and not SENSITIVE_FIELD.search(name)}
        selected = self.sudo().orm_write_field_ids if writing else self.sudo().orm_field_ids
        names = selected.mapped('name')
        if not names:
            return {}
        metadata = Model.fields_get(names, attributes=['string', 'type', 'required', 'selection'])
        for item in selected:
            name = item.name
            field = Model._fields.get(name)
            if (item.model != Model._name or name not in metadata or not field
                    or field.type not in FIELD_TYPES or SENSITIVE_FIELD.search(name)
                    or (writing and (name in PROTECTED_FIELDS or field.readonly
                                     or field.compute or field.related))):
                raise ValidationError(self.env._('The selected field is unavailable: %s', name))
        return metadata

    def _orm_catalog_metadata(self, Model):
        """
        Readable fields worth listing to the planner.

        Explicitly configured fields are listed as they are. With all readable fields enabled, keep
        stored, related or searchable fields and skip helper fields (CATALOG_SKIP_FIELD) and links to
        technical configuration models; every readable field stays valid in arguments.
        """
        metadata = self._orm_metadata(Model)
        if not self.orm_all_fields:
            return metadata
        catalog = {}
        for name, meta in metadata.items():
            field = Model._fields.get(name)
            if (not field or CATALOG_SKIP_FIELD.search(name)
                    or (field.type == 'many2one' and (field.comodel_name or '').startswith(CATALOG_SKIP_COMODELS))):
                continue
            if field.store or field.related or field.search:
                catalog[name] = meta
        return catalog

    def _orm_domain(self, Model, value, configured=False):
        if not isinstance(value, list) or len(value) > 30:
            raise ValidationError(self.env._('Use at most 30 simple filter conditions.'))
        readable = Model.fields_get(attributes=['type'])
        allowed = set(readable) if configured else set(self._orm_metadata(Model)) | {'id'}
        for term in value:
            if isinstance(term, str) and term in ('&', '|', '!'):
                continue
            if (not isinstance(term, (list, tuple)) or len(term) != 3
                    or not isinstance(term[0], str)
                    or not self._orm_domain_field(Model, term[0], allowed)
                    or not isinstance(term[1], str) or term[1] not in FILTER_OPERATORS):
                raise ValidationError(self.env._('A filter uses an unavailable field or operator.'))
            val = term[2]
            if isinstance(val, dict) or (isinstance(val, (list, tuple)) and (
                    len(val) > 100 or any(isinstance(v, (dict, list, tuple)) for v in val))):
                raise ValidationError(self.env._('The filter value is invalid.'))
            if ((term[1] in ('in', 'not in') and not isinstance(val, (list, tuple)))
                    or (term[1] not in ('in', 'not in') and isinstance(val, (list, tuple)))
                    or (term[1] in ('like', 'ilike') and not isinstance(val, str))
                    or (isinstance(val, str) and len(val) > 4000)):
                raise ValidationError(self.env._('The filter value is invalid.'))
        try:
            return Domain(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(self.env._('The filter expression is invalid.')) from exc

    def _orm_domain_field(self, Model, path, allowed):
        """Allow short, readable many2one paths such as state_id.name."""
        parts = path.split('.')
        if (not parts or len(parts) > FILTER_PATH_DEPTH or parts[0] not in allowed
                or any(not part.isidentifier() or SENSITIVE_FIELD.search(part) for part in parts)):
            return False
        Current = Model
        for index, name in enumerate(parts):
            metadata = Current.fields_get([name], attributes=['type'])
            field = Current._fields.get(name)
            if name not in metadata or not field:
                return False
            if index == len(parts) - 1:
                return field.type in FIELD_TYPES or name == 'id'
            if field.type != 'many2one' or not field.comodel_name or field.comodel_name not in self.env:
                return False
            Current = self.env[field.comodel_name].with_context(
                allowed_company_ids=[self.env.company.id],
            )
            try:
                Current.check_access('read')
            except AccessError:
                return False
        return True

    def _orm_requested_fields(self, Model, value=None):
        metadata = self._orm_metadata(Model)
        if value is None:
            catalog = self._orm_catalog_metadata(Model)
            preferred = ['id', 'name', 'display_name', 'company_id', 'state', 'active']
            names = [name for name in preferred if name in catalog]
            names += [name for name in sorted(catalog) if name not in names]
            return names[:12]
        if (not isinstance(value, list) or not 1 <= len(value) <= 12
                or len(set(value)) != len(value)
                or any(not isinstance(name, str) or name not in metadata for name in value)):
            raise ValidationError(self.env._('Choose 1 to 12 readable fields returned by this tool.'))
        return value

    def _orm_order(self, Model, value):
        if value is None:
            return 'id desc'
        if not isinstance(value, str) or len(value) > 120:
            raise ValidationError(self.env._('The requested sort order is invalid.'))
        allowed = set(self._orm_metadata(Model)) | {'id'}
        parts = [part.strip() for part in value.split(',')]
        if (not 1 <= len(parts) <= 3 or any(not part for part in parts)):
            raise ValidationError(self.env._('Use at most three sort fields.'))
        normalized = []
        for part in parts:
            match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)(?:\s+(asc|desc))?', part, flags=re.I)
            if not match or match.group(1) not in allowed:
                raise ValidationError(self.env._('The requested sort field is unavailable.'))
            normalized.append(match.group(1) + (' ' + match.group(2).lower() if match.group(2) else ''))
        return ', '.join(normalized)

    def _orm_json(self, text, expected):
        try:
            result = json.loads(text or ('{}' if expected is dict else '[]'))
            if not isinstance(result, expected):
                raise ValueError('wrong type')
            return result
        except (TypeError, ValueError) as exc:
            raise ValidationError(self.env._('Enter valid JSON for the fixed values or filter.')) from exc

    def _orm_scope(self, Model, extra=None):
        # Administrators configure a fixed scope; a caller can only narrow it.
        domain = self._orm_domain(Model, self._orm_json(self.orm_domain, list), configured=True)
        if 'company_id' in Model._fields:
            domain &= Domain('company_id', 'in', [False, self.env.company.id])
        if extra is not None:
            domain &= self._orm_domain(Model, extra)
        return domain

    def _orm_values(self, Model, values):
        metadata = self._orm_metadata(Model, writing=True)
        if not isinstance(values, dict) or set(values) - metadata.keys():
            raise ValidationError(self.env._('Only the configured writable fields may be changed.'))
        result, related = {}, []
        for name, value in values.items():
            field = Model._fields[name]
            if value is None:
                value = False
            if value is False and field.type != 'boolean':
                if field.required:
                    raise ValidationError(self.env._('A required field cannot be cleared: %s', name))
                result[name] = False
                continue
            valid = True
            if field.type in ('char', 'text'):
                valid = isinstance(value, str) and len(value) <= min(getattr(field, 'size', None) or 4000, 4000)
            elif field.type in ('integer', 'many2one'):
                valid = type(value) is int and abs(value) <= 2147483647
            elif field.type in ('float', 'monetary'):
                valid = type(value) in (int, float) and math.isfinite(value) and abs(value) < 1e12
            elif field.type == 'boolean':
                valid = type(value) is bool
            elif field.type == 'selection':
                valid = type(value) in (str, int) and value in dict(metadata[name].get('selection', []))
            elif field.type in ('date', 'datetime'):
                try:
                    parser = fields.Date if field.type == 'date' else fields.Datetime
                    valid = isinstance(value, str) and parser.to_string(parser.to_date(value) if
                        field.type == 'date' else parser.to_datetime(value)) == value
                except (TypeError, ValueError):
                    valid = False
            if not valid:
                raise ValidationError(self.env._('The value for field %s is invalid.', name))
            if field.type == 'many2one':
                if value <= 0:
                    raise ValidationError(self.env._('Choose an existing related record.'))
                record = self.env[field.comodel_name].browse(value).exists()
                record.check_access('read')
                if not record or ('company_id' in record._fields and record.company_id
                                  and record.company_id != self.env.company):
                    raise AccessError(self.env._('The related record is unavailable in this company.'))
                related.append(record)
            result[name] = value
        return result, related

    def _orm_check(self):
        Model = self._orm_model()
        Model.check_access(ORM_ACCESS[self.orm_operation])
        Model.check_access('read')
        self._orm_metadata(Model)
        self._orm_metadata(Model, writing=True)
        self._orm_scope(Model)
        if not 1 <= self.orm_limit <= 100:
            raise ValidationError(self.env._('The query limit must be between 1 and 100.'))
        if self.orm_operation in ('search', 'read') and not self._orm_metadata(Model):
            raise ValidationError(self.env._('Select at least one readable field.'))
        if self.orm_operation in ('create', 'write') and not self.sudo().orm_write_field_ids:
            raise ValidationError(self.env._('Select at least one writable field.'))
        if self.orm_operation in ('archive', 'restore') and 'active' not in Model._fields:
            raise ValidationError(self.env._('This model does not support archiving.'))
        self._orm_values(Model, self._orm_json(self.orm_defaults, dict))
        return Model

    def _orm_schema(self):
        Model = self._orm_check()
        properties = {}
        if self.orm_operation in ('search', 'count'):
            properties['domain'] = {
                'type': 'array',
                'description': (
                    'Optional Odoo domain filters on readable fields. Short many2one paths are allowed, '
                    'for example [["state_id.name", "ilike", "Shandong"]].'
                ),
            }
        if self.orm_operation in ('search', 'read'):
            properties['fields'] = {
                'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 12,
                'description': 'Optional readable fields to return. Omit for a compact default set.',
            }
        if self.orm_operation == 'search':
            properties['order'] = {
                'type': 'string',
                'description': 'Optional comma-separated readable fields with asc or desc.',
            }
        if self.orm_operation not in ('create', 'search', 'count'):
            properties['record_id'] = {'type': 'integer', 'description': 'One existing record ID; never guess.'}
        if self.orm_operation in ('create', 'write'):
            items = {}
            defaults = self._orm_json(self.orm_defaults, dict)
            for name, meta in self._orm_metadata(Model, writing=True).items():
                kind = {'many2one': 'integer', 'integer': 'integer', 'boolean': 'boolean',
                        'float': 'number', 'monetary': 'number'}.get(meta['type'], 'string')
                items[name] = {'type': kind, 'description': meta['string']}
                if meta['type'] == 'selection':
                    items[name]['enum'] = [key for key, _label in meta.get('selection', [])]
                    if items[name]['enum'] and all(type(key) is int for key in items[name]['enum']):
                        items[name]['type'] = 'integer'
                if meta['type'] == 'many2one':
                    items[name]['description'] += ' (existing %s record ID)' % Model._fields[name].comodel_name
                if meta['type'] == 'datetime':
                    items[name]['description'] += ' (UTC, YYYY-MM-DD HH:MM:SS)'
                if meta['type'] == 'date':
                    items[name]['description'] += ' (YYYY-MM-DD)'
                if not meta.get('required'):
                    items[name]['type'] = [items[name]['type'], 'null']
                    if 'enum' in items[name]:
                        items[name]['enum'].append(None)
            required = []
            if self.orm_operation == 'create':
                model_defaults = Model.default_get(list(items))
                required = [name for name, meta in self._orm_metadata(Model, True).items()
                            if meta.get('required') and name not in defaults and name not in model_defaults]
            properties['values'] = {'type': 'object', 'properties': items, 'required': required,
                                    'additionalProperties': False}
        required = [key for key in properties if key in ('record_id', 'values')]
        return {'type': 'object', 'properties': properties, 'required': required,
                'additionalProperties': False}

    def _orm_prepare(self, arguments):
        """Validate and prepare without calling create/write/unlink for a preview."""
        Model = self._orm_check()
        schema = self._orm_schema()
        if (not isinstance(arguments, dict) or set(arguments) - schema['properties'].keys()
                or set(schema['required']) - arguments.keys()):
            raise ValidationError(self.env._('The tool arguments do not match its configuration.'))
        operation = self.orm_operation
        if operation in ('search', 'count'):
            domain = self._orm_scope(Model, arguments.get('domain', []))
            normalized = {'domain': list(domain)}
            if operation == 'search':
                normalized['fields'] = self._orm_requested_fields(Model, arguments.get('fields'))
                normalized['order'] = self._orm_order(Model, arguments.get('order'))
            return normalized, [], ''
        normalized, related = {}, []
        records = Model
        if operation != 'create':
            record_id = arguments['record_id']
            if type(record_id) is not int or record_id <= 0:
                raise ValidationError(self.env._('Choose one existing record.'))
            domain = self._orm_scope(Model) & Domain('id', '=', record_id)
            records = Model.search(domain, limit=1)
            if not records:
                raise AccessError(self.env._('This record is outside the allowed tool scope.'))
            records.check_access(ORM_ACCESS[operation])
            if operation == 'read':
                normalized['fields'] = self._orm_requested_fields(Model, arguments.get('fields'))
        if operation in ('create', 'write'):
            values, related = self._orm_values(Model, arguments['values'])
            fixed, fixed_related = self._orm_values(Model, self._orm_json(self.orm_defaults, dict))
            if any(name in values and values[name] != value for name, value in fixed.items()):
                raise ValidationError(self.env._('A fixed value cannot be overridden.'))
            values.update(fixed)
            related += fixed_related
            if not values:
                raise ValidationError(self.env._('Provide at least one field value.'))
            if operation == 'create':
                defaults = Model.default_get([name for name, field in Model._fields.items()
                                              if field.required and not field.compute])
                missing = [name for name, field in Model._fields.items()
                           if field.required and not field.compute and name not in values
                           and name not in defaults and name not in ('id', 'company_id')
                           and name not in PROTECTED_FIELDS]
                if missing:
                    raise UserError(self.env._('Required fields need values or defaults: %s', ', '.join(missing)))
                if 'company_id' in Model._fields:
                    values['company_id'] = self.env.company.id
            normalized['values'] = values
        if records:
            normalized['record_id'] = records.id
        preview = self.env._('%(tool)s — %(company)s', tool=self.name, company=self.env.company.display_name)
        if records:
            preview += '\n' + self.env._('Record: %(name)s (ID: %(id)s)', name=records.display_name, id=records.id)
        for name, value in normalized.get('values', {}).items():
            label = Model.fields_get([name], attributes=['string'])[name]['string']
            old = records.read([name])[0][name] if records else None
            preview += '\n%s: %s%s' % (label, (str(old) + ' → ') if records else '', value)
        return normalized, [*records, *related], preview

    def _orm_read_records(self, records, names):
        """Isolate inaccessible computed fields without elevating business access."""
        records.check_access('read')
        names = sorted(set(names))
        try:
            with self.env.cr.savepoint():
                return records.read(names), []
        except AccessError:
            if not self.orm_all_fields or not names:
                raise
            if len(names) == 1:
                return [{'id': record.id} for record in records], names
        # Split only a failed batch; successful reads remain batched across records.
        middle = len(names) // 2
        left, left_skipped = self._orm_read_records(records, names[:middle])
        right, right_skipped = self._orm_read_records(records, names[middle:])
        return [dict(a, **b) for a, b in zip(left, right)], left_skipped + right_skipped

    def _orm_fingerprint(self, records):
        config = [self.id, self.execution_type, self._orm_model_name(), self.orm_operation,
                  self.orm_all_fields, self.sudo().orm_field_ids.ids, self.sudo().orm_write_field_ids.ids, self.orm_domain,
                  self.orm_defaults, self.orm_limit, str(self.write_date)]
        names = set(self._orm_metadata(self._orm_model())) | set(self._orm_metadata(self._orm_model(), True))
        snapshot = [[record._name, record.id, str(record.write_date),
                     self._orm_read_records(record, names | ({'active'} if 'active' in record._fields else set()))
                     if record._name == self._orm_model_name() and names else None]
                    for record in records]
        return hashlib.sha256(json.dumps([config, snapshot], sort_keys=True, default=str).encode()).hexdigest()

    def _orm_query(self, arguments):
        normalized, _records, _preview = self._orm_prepare(arguments)
        Model = self._orm_model()
        domain = normalized.get('domain') or list(self._orm_scope(Model))
        if self.orm_operation == 'read':
            domain = list(Domain(domain) & Domain('id', '=', normalized['record_id']))
        total = Model.search_count(domain)
        result = {'count': total}
        if self.orm_operation != 'count':
            records = Model.search(
                domain, limit=self.orm_limit,
                order=normalized.get('order') or 'id desc',
            )
            names = normalized.get('fields') or self._orm_requested_fields(Model)
            result['records'], skipped = self._orm_read_records(records, names)
            if skipped:
                result['unavailable_fields'] = skipped
        return self.env._('Query result:') + '\n```json\n' + json.dumps(result, ensure_ascii=False, default=str) + '\n```'

    def _orm_catalog_description(self, fields_reference=None, with_schema=True):
        """Expose runtime-safe field metadata to the planner without admin field configuration."""
        self.ensure_one()
        Model = self._orm_check()
        catalog = self.name + ': ' + (self.description or '') + '\nmodel: ' + Model._name
        if with_schema:
            catalog += '\narguments schema: ' + json.dumps(self._orm_schema(), ensure_ascii=False)
        if fields_reference:
            return catalog + '\nruntime readable fields: same model catalog as ' + fields_reference
        metadata = self._orm_catalog_metadata(Model)
        fields_catalog = []
        for name, meta in sorted(metadata.items()):
            entry = f"{name}|{meta.get('type')}|{meta.get('string') or name}"
            if Model._fields[name].type == 'many2one':
                entry += '|' + Model._fields[name].comodel_name
            fields_catalog.append(entry)
        return (
            catalog
            + '\nruntime readable fields (name|type|label|related model): ' + '; '.join(fields_catalog)
            + '\nFilters may use a listed many2one field followed by a readable related field, '
              'for example state_id.name. The application validates every path under the current user.'
        )

    def _orm_execute(self, normalized):
        Model = self._orm_model()
        if self.orm_operation == 'create':
            record = Model.create(normalized['values'])
            if not Model.search(self._orm_scope(Model) & Domain('id', '=', record.id)):
                raise UserError(self.env._('The new record does not match the configured tool scope.'))
            return record
        record = Model.browse(normalized['record_id'])
        if self.orm_operation == 'write':
            record.write(normalized['values'])
        elif self.orm_operation in ('archive', 'restore'):
            record.write({'active': self.orm_operation == 'restore'})
        elif self.orm_operation == 'unlink':
            record.unlink()
        return record

    @api.constrains('execution_type', 'model_id', 'orm_operation', 'orm_all_fields', 'orm_field_ids',
                    'orm_write_field_ids', 'orm_domain', 'orm_defaults', 'orm_limit')
    def _check_orm_configuration(self):
        for tool in self.filtered(lambda item: item.execution_type == 'orm'):
            tool._orm_check()

    @api.onchange('model_id')
    def _onchange_orm_model(self):
        if self.execution_type == 'orm':
            self.orm_field_ids = self.model_id.field_id.filtered(lambda f: f.name == 'name')
            self.orm_write_field_ids = False
            self.orm_domain, self.orm_defaults = '[]', '{}'

    execution_type = fields.Selection([('adapter', 'Business Template'), ('orm', 'ORM Operation')],
                                       default='adapter', required=True)
    key = fields.Char(default=lambda self: 'orm_' + uuid.uuid4().hex)
    model_id = fields.Many2one(readonly=False)
    orm_all_fields = fields.Boolean(string='All Readable Fields', help=ALL_READABLE_FIELDS_HELP)
    orm_defaults = fields.Text(string='Fixed Values (JSON)', default='{}')
    orm_domain = fields.Char(string='Record Filter', default='[]')
    orm_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_tool_read_field_rel',
                                    string='Readable Fields')
    orm_limit = fields.Integer(string='Maximum Query Results', default=20)
    orm_model_name = fields.Char(related='model_id.model')
    orm_operation = fields.Selection(ORM_OPERATIONS, string='Operation', default='search', required=True)
    orm_write_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_tool_write_field_rel',
                                          string='Writable Fields')

    def action_open_builder(self):
        self._require_admin()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Configure AI Tools'),
                'res_model': 'qing.ai.tool.builder', 'view_mode': 'form', 'target': 'new'}
