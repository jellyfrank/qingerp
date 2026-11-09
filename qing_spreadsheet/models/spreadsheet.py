import copy
import hashlib
import json
import secrets
import uuid
from datetime import timedelta

from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import workbook


LEASE_SECONDS = 120
PROTECTED_FIELDS = {
    'spreadsheet_data', 'spreadsheet_binary_data', 'thumbnail', 'content_version',
    'lease_digest', 'lease_until', 'lease_user_id', 'binding_model', 'binding_res_id', 'binding_version',
}
MANAGEMENT_FIELDS = {'owner_id', 'reader_ids', 'editor_ids', 'company_id', 'is_template', 'active'}


class QingSpreadsheet(models.Model):
    _name = 'qing.spreadsheet'
    _description = 'Qing Workbook'
    _inherit = ['spreadsheet.mixin']
    _order = 'write_date desc, id desc'
    _check_company_auto = True

    def _is_manager(self):
        return self.env.su or self.env.user.has_group('qing_spreadsheet.group_spreadsheet_manager')

    def _check_owner(self):
        self.check_access('write')
        if not self._is_manager() and any(record.owner_id != self.env.user for record in self):
            raise AccessError(_('Only the owner or a spreadsheet manager can change sharing and ownership.'))

    def _lock(self, operation='write'):
        self.ensure_one()
        self.check_access(operation)
        self.env.cr.execute('SELECT id FROM qing_spreadsheet WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        self.check_access(operation)

    def _check_edit(self):
        self.check_access('write')
        if not self.active:
            raise UserError(_('Restore this archived workbook before editing.'))
        if self.binding_model:
            self._bound_record('write')

    def _bound_record(self, operation='read'):
        self.ensure_one()
        if not self.binding_model or self.binding_model not in self.env:
            raise UserError(_('This workbook is not connected to a business record.'))
        record = self.env[self.binding_model].browse(self.binding_res_id).exists()
        if not record or not getattr(record, '_qing_spreadsheet_enabled', False):
            raise UserError(_('The business record adapter is unavailable.'))
        record.check_access(operation)
        if 'company_id' in record._fields and record.company_id and record.company_id != self.company_id:
            raise AccessError(_('The record belongs to another company.'))
        return record

    def _check_lease(self, token):
        if not isinstance(token, str) or len(token) > 128:
            raise UserError(_('The editing session has expired. Reopen the workbook.'))
        private = self.sudo()
        digest = hashlib.sha256(token.encode()).hexdigest()
        if (self.lease_user_id != self.env.user or not self.lease_until or self.lease_until <= fields.Datetime.now()
                or not secrets.compare_digest(private.lease_digest or '', digest)):
            raise UserError(_('The editing session has expired or another editor owns the lock.'))

    def _internal_write(self, values):
        return super(QingSpreadsheet, self.sudo().with_context(_qing_spreadsheet_internal=workbook.INTERNAL_WRITE)).write(values)

    def _validate_sources(self, data):
        definitions = list(data.get('lists', {}).values())
        definitions += [value for value in data.get('pivots', {}).values() if value.get('type') == 'ODOO']
        charts = list(data.get('charts', {}).values())
        charts += [figure.get('data', {}) for sheet in data['sheets'] for figure in sheet.get('figures', [])]
        for chart in charts:
            source = chart.get('dataSource', {})
            if not isinstance(source, dict):
                raise ValidationError(_('Invalid chart data source.'))
            if source.get('type') != 'odoo':
                continue
            metadata = source.get('metaData', {})
            search = source.get('searchParams', {})
            definition = {'model': metadata.get('resModel'), 'domain': search.get('domain', []),
                          'columns': [field.split(':')[0] for field in metadata.get('groupBy', []) + search.get('groupBy', [])],
                          'measures': [{'fieldName': (metadata.get('measure') or '__count').split(':')[0]}]}
            definitions.append(definition)
            search['context'] = {'allowed_company_ids': [self.company_id.id]}
            source['searchParams'] = search
        models_used = {definition.get('model') for definition in definitions}
        policy_model = self.env['qing.spreadsheet.source']
        domain = [('model_name', 'in', list(models_used))]
        policies = {policy.model_name: policy for policy in policy_model.search(domain)}
        for definition in definitions:
            policy = policies.get(definition.get('model'))
            if not policy:
                raise ValidationError(_('This workbook uses a data source that is not enabled.'))
            policy._validate_definition(definition, self.company_id.id)
        return data

    def _validated_data(self, data):
        return self._validate_sources(workbook.validate(copy.deepcopy(data)))

    def _create_revision(self, data, request_id, label='', operation='save', input_digest=None, result=None):
        serialized = workbook.encode(data)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        return self.env['qing.spreadsheet.revision'].sudo().with_context(
            _qing_spreadsheet_internal=workbook.INTERNAL_WRITE,
        ).create({
            'workbook_id': self.id, 'version': self.content_version, 'data': serialized,
            'request_id': request_id, 'digest': digest, 'input_digest': input_digest or digest,
            'operation': operation, 'result_json': json.dumps(result or {}),
            'author_id': self.env.uid, 'label': label,
        })

    def _request_id(self, value):
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError(_('A valid save request identifier is required.')) from exc

    def _saved_request(self, request_id, digest=None, operation=None):
        domain = [('workbook_id', '=', self.id), ('request_id', '=', request_id)]
        revision = self.env['qing.spreadsheet.revision'].search(domain, limit=1)
        if revision and digest and revision.input_digest != digest:
            raise ValidationError(_('This request identifier was already used for different content.'))
        if revision and operation and revision.operation != operation:
            raise ValidationError(_('This request identifier was used for another operation.'))
        return revision

    def _save(self, data, version, token, request_id, label=''):
        self._lock()
        self._check_edit()
        request_id = self._request_id(request_id)
        data = self._validated_data(data)
        serialized = workbook.encode(data)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        previous = self._saved_request(request_id, digest, 'save')
        if previous:
            return {'version': previous.version, 'request_id': request_id, 'replayed': True,
                    'current_version': self.content_version}
        self._check_lease(token)
        if type(version) is not int or version != self.content_version:
            raise UserError(_('The workbook has changed. Reload it before saving your changes.'))
        self._internal_write({'spreadsheet_data': serialized, 'content_version': version + 1,
                              'lease_until': fields.Datetime.now() + timedelta(seconds=LEASE_SECONDS)})
        self._create_revision(data, request_id, label)
        return {'version': self.content_version, 'request_id': request_id, 'replayed': False,
                'current_version': self.content_version}

    @api.depends_context('uid')
    @api.depends('owner_id')
    def _compute_can_manage(self):
        for record in self:
            record.can_manage = record._is_manager() or record.owner_id == self.env.user

    active = fields.Boolean(default=True)
    binding_model = fields.Char(readonly=True, copy=False)
    binding_res_id = fields.Integer(readonly=True, copy=False)
    binding_version = fields.Char(readonly=True, copy=False)
    can_manage = fields.Boolean(compute='_compute_can_manage')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    content_version = fields.Integer(default=0, readonly=True, copy=False)
    description = fields.Text()
    editor_ids = fields.Many2many('res.users', 'qing_sheet_editors_rel', string='Editors', domain=[('share', '=', False)])
    favorite_user_ids = fields.Many2many('res.users', 'qing_sheet_favorites_rel', string='Favorites', copy=False)
    is_favorite = fields.Boolean(compute='_compute_is_favorite')
    is_template = fields.Boolean(default=False, string='Template')
    lease_digest = fields.Char(readonly=True, copy=False, groups='base.group_no_one')
    lease_until = fields.Datetime(readonly=True, copy=False)
    lease_user_id = fields.Many2one('res.users', readonly=True, copy=False, ondelete='set null')
    name = fields.Char(required=True, default=lambda self: _('Untitled spreadsheet'))
    owner_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, ondelete='restrict', index=True)
    reader_ids = fields.Many2many('res.users', 'qing_sheet_readers_rel', string='Viewers', domain=[('share', '=', False)])
    revision_ids = fields.One2many('qing.spreadsheet.revision', 'workbook_id', readonly=True)
    spreadsheet_binary_data = fields.Binary(attachment=False)
    tag = fields.Char(string='Category')

    @api.depends_context('uid')
    @api.depends('favorite_user_ids')
    def _compute_is_favorite(self):
        for record in self:
            record.is_favorite = self.env.user in record.favorite_user_ids

    @api.constrains('owner_id', 'editor_ids', 'reader_ids', 'company_id')
    def _check_members(self):
        for record in self:
            members = record.owner_id | record.editor_ids | record.reader_ids
            if any(user.share or record.company_id not in user.company_ids for user in members):
                raise ValidationError(_('Workbook members must be internal users with access to its company.'))

    @api.model_create_multi
    def create(self, vals_list):
        internal = self.env.context.get('_qing_spreadsheet_internal') is workbook.INTERNAL_WRITE
        protected = PROTECTED_FIELDS | {'revision_ids', 'favorite_user_ids'}
        if not internal and any(f'default_{name}' in self.env.context for name in protected):
            raise AccessError(_('Workbook content cannot be supplied through context defaults.'))
        for values in vals_list:
            if not internal and (PROTECTED_FIELDS | {'revision_ids', 'favorite_user_ids'}) & values.keys():
                raise AccessError(_('Use the workbook import and save actions to change its content.'))
            if not self._is_manager() and values.get('owner_id', self.env.context.get('default_owner_id', self.env.uid)) != self.env.uid:
                raise AccessError(_('You can only create workbooks owned by yourself.'))
            if values.get('company_id', self.env.company.id) not in self.env.companies.ids:
                raise AccessError(_('Choose an allowed company.'))
        records = super().create(vals_list)
        for record in records:
            data = record._validated_data(json.loads(record.spreadsheet_data))
            record._create_revision(data, str(uuid.uuid4()), _('Created'))
        return records

    def write(self, vals):
        if self.env.context.get('_qing_spreadsheet_internal') is workbook.INTERNAL_WRITE:
            return super().write(vals)
        if PROTECTED_FIELDS & vals.keys() or 'revision_ids' in vals or 'favorite_user_ids' in vals:
            raise AccessError(_('Use the workbook actions to change content, versions or favorites.'))
        if MANAGEMENT_FIELDS & vals.keys():
            self._check_owner()
        if 'company_id' in vals and any(record.company_id.id != vals['company_id'] for record in self):
            raise ValidationError(_('A saved workbook cannot be moved to another company.'))
        return super().write(vals)

    def unlink(self):
        self._check_owner()
        for record in self:
            record._lock()
            if record.lease_until and record.lease_until > fields.Datetime.now():
                raise UserError(_('Close the active editing session before deleting this workbook.'))
        return super().unlink()

    def copy(self, default=None):
        self.ensure_one()
        self.check_access('read')
        values = {
            'name': _('%s (copy)', self.name), 'owner_id': self.env.uid,
            'editor_ids': [Command.clear()], 'reader_ids': [Command.clear()],
            'is_template': False, 'revision_ids': [], 'content_version': 0,
            'binding_model': False, 'binding_res_id': 0, 'binding_version': False,
        }
        values.update(default or {})
        # External callers may customize only harmless copy metadata.
        if default and set(default) - {'name', 'description', 'tag'}:
            raise AccessError(_('Only the copy name, description and category can be changed.'))
        return super(QingSpreadsheet, self.with_context(_qing_spreadsheet_internal=workbook.INTERNAL_WRITE)).copy(values)

    def action_open(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.client', 'tag': 'qing_spreadsheet.editor', 'name': self.name,
                'params': {'workbook_id': self.id}}

    def action_duplicate(self):
        return self.copy().action_open()

    def action_toggle_favorite(self):
        self.ensure_one()
        self.check_access('read')
        command = Command.unlink(self.env.uid) if self.is_favorite else Command.link(self.env.uid)
        # The only elevated update changes the current reader's own favorite relation.
        self.sudo()._internal_write({'favorite_user_ids': [command]})
        return self.is_favorite

    def open_workbook(self):
        self.ensure_one()
        self.check_access('read')
        if self.binding_model:
            self._bound_record()
        can_edit = self.active and self.has_access('write')
        data = json.loads(self.spreadsheet_data)
        return {'id': self.id, 'name': self.name, 'data': data, 'version': self.content_version,
                'company_id': self.company_id.id, 'can_edit': can_edit,
                'can_manage': self._is_manager() or self.owner_id == self.env.user,
                'can_export': self.env.user.has_group('base.group_allow_export'),
                'bound': bool(self.binding_model), 'binding_version': self.binding_version,
                'lease_user': self.lease_user_id.display_name if self.lease_user_id else False,
                'lease_until': fields.Datetime.to_string(self.lease_until)}

    def acquire_lease(self, token=None):
        self._lock()
        self._check_edit()
        now = fields.Datetime.now()
        if self.lease_until and self.lease_until > now:
            if token:
                self._check_lease(token)
            else:
                return {'acquired': False, 'user': self.lease_user_id.display_name,
                        'expires': fields.Datetime.to_string(self.lease_until)}
        if token is not None and (not isinstance(token, str) or not 16 <= len(token) <= 128):
            raise ValidationError(_('Invalid editing session token.'))
        token = secrets.token_urlsafe(32)
        self._internal_write({'lease_digest': hashlib.sha256(token.encode()).hexdigest(),
                              'lease_user_id': self.env.uid, 'lease_until': now + timedelta(seconds=LEASE_SECONDS)})
        return {'acquired': True, 'token': token, 'version': self.content_version, 'ttl': LEASE_SECONDS}

    def renew_lease(self, token):
        self._lock()
        self._check_edit()
        self._check_lease(token)
        self._internal_write({'lease_until': fields.Datetime.now() + timedelta(seconds=LEASE_SECONDS)})
        return {'version': self.content_version, 'ttl': LEASE_SECONDS}

    def release_lease(self, token):
        self._lock()
        self._check_lease(token)
        self._internal_write({'lease_digest': False, 'lease_until': False, 'lease_user_id': False})
        return True

    def save_workbook(self, data, version, token, request_id, label=''):
        return self._save(data, version, token, request_id, str(label)[:200])

    def save_status(self, request_id):
        self.ensure_one()
        self.check_access('read')
        revision = self._saved_request(self._request_id(request_id))
        return {'saved': bool(revision), 'version': revision.version if revision else False,
                'current_version': self.content_version,
                'operation': revision.operation if revision else False,
                'result': json.loads(revision.result_json or '{}') if revision else {}}

    def list_versions(self):
        self.ensure_one()
        self.check_access('read')
        domain = [('workbook_id', '=', self.id)]
        return self.env['qing.spreadsheet.revision'].search_read(domain,
            ['version', 'label', 'author_id', 'create_date'], limit=100)

    def restore_version(self, revision_id, version, token, request_id):
        self.ensure_one()
        self.check_access('write')
        revision = self.env['qing.spreadsheet.revision'].browse(revision_id).exists()
        revision.check_access('read')
        if not revision or revision.workbook_id != self:
            raise AccessError(_('This version does not belong to the workbook.'))
        result = self._save(json.loads(revision.data), version, token, request_id, _('Restored version %s', revision.version))
        result['data'] = json.loads(self.spreadsheet_data)
        return result

    @api.model
    def import_workbook(self, name, data, company_id=None):
        data = workbook.validate(data)
        company_id = company_id or self.env.company.id
        record = self.with_context(_qing_spreadsheet_internal=workbook.INTERNAL_WRITE).create({
            'name': str(name)[:200] or _('Imported spreadsheet'), 'company_id': company_id,
        })
        data = record._validated_data(data)
        record._internal_write({'spreadsheet_data': workbook.encode(data)})
        record._save(data, 0, record.acquire_lease()['token'], str(uuid.uuid4()), _('Imported'))
        record._internal_write({'lease_digest': False, 'lease_until': False, 'lease_user_id': False})
        return record.action_open()

    def preview_record(self, data, version, token):
        self._lock()
        self._check_edit()
        self._check_lease(token)
        if version != self.content_version:
            raise UserError(_('The workbook has changed. Reload before reviewing changes.'))
        record = self._bound_record('write')
        record._qing_spreadsheet_lock()
        if str(record._qing_spreadsheet_fingerprint()) != self.binding_version:
            raise UserError(_('The business record has changed. Reload its data before applying changes.'))
        return record._qing_spreadsheet_validate(self._validated_data(data))

    def apply_record(self, data, version, token, request_id):
        """Commit a draft and its business change atomically; replay the same request safely."""
        self._lock()
        self._check_edit()
        data = self._validated_data(data)
        digest = hashlib.sha256(workbook.encode(data).encode()).hexdigest()
        request_id = self._request_id(request_id)
        previous = self._saved_request(request_id, digest, 'apply')
        if previous:
            return json.loads(previous.result_json)
        self._check_lease(token)
        if type(version) is not int or version != self.content_version:
            raise UserError(_('The workbook has changed. Reload before applying changes.'))
        record = self._bound_record('write')
        record._qing_spreadsheet_lock()
        if str(record._qing_spreadsheet_fingerprint()) != self.binding_version:
            raise UserError(_('The business record has changed. Reload its data before applying changes.'))
        review = record._qing_spreadsheet_validate(data)
        if review.get('errors'):
            raise ValidationError(_('Fix the reported cell errors before applying changes.'))
        record._qing_spreadsheet_apply(data)
        normalized = self._validated_data(record._qing_spreadsheet_load())
        fingerprint = str(record._qing_spreadsheet_fingerprint())
        self._internal_write({'spreadsheet_data': workbook.encode(normalized), 'content_version': version + 1,
                              'binding_version': fingerprint,
                              'lease_until': fields.Datetime.now() + timedelta(seconds=LEASE_SECONDS)})
        result = {'version': version + 1, 'binding_version': fingerprint, 'request_id': request_id}
        self._create_revision(normalized, request_id, _('Applied to record'), 'apply', digest, result)
        return result

    def reload_record(self, version, token, request_id):
        self._lock()
        self._check_edit()
        request_id = self._request_id(request_id)
        previous = self._saved_request(request_id, operation='reload')
        if previous:
            return json.loads(previous.result_json)
        self._check_lease(token)
        if type(version) is not int or version != self.content_version:
            raise UserError(_('The workbook has changed. Reload before replacing its draft.'))
        record = self._bound_record('write')
        record._qing_spreadsheet_lock()
        data = self._validated_data(record._qing_spreadsheet_load())
        fingerprint = str(record._qing_spreadsheet_fingerprint())
        self._internal_write({'spreadsheet_data': workbook.encode(data), 'content_version': version + 1,
                              'binding_version': fingerprint})
        result = {'version': version + 1, 'binding_version': fingerprint, 'request_id': request_id}
        self._create_revision(data, request_id, _('Reloaded business record'), 'reload', result=result)
        return result

    def query_data(self, model_name, method, args, kwargs):
        self.ensure_one()
        self.check_access('read')
        if self.binding_model:
            self._bound_record('read')
        policy = self.env['qing.spreadsheet.source'].search([('model_name', '=', model_name)], limit=1)
        if not policy:
            raise AccessError(_('This data source is not enabled.'))
        return policy._query(method, args, kwargs, self.company_id)
