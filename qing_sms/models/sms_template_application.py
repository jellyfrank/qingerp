import base64
import json
from datetime import timedelta
from uuid import uuid4

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from .constants import fingerprint
from .sms_blueprint import PLACEHOLDER


APPLICATION_STATES = [
    ('draft', 'Draft'), ('queued', 'Queued'), ('unknown', 'Outcome unknown'),
    ('pending', 'Under review'), ('approved', 'Approved'), ('rejected', 'Rejected'),
    ('failed', 'Failed'), ('canceled', 'Canceled'),
]
APPLICATION_LOCK_NAMESPACE = 734913


class SmsTemplateApplication(models.Model):
    _name = 'qing.sms.template.application'
    _description = 'Domestic SMS template application'
    _check_company_auto = True
    _order = 'id desc'

    @api.onchange('blueprint_id')
    def _onchange_blueprint(self):
        for application in self:
            if application.blueprint_id:
                application.name = application.blueprint_id.name
                application.content = application.blueprint_id.content
                application.category = application.blueprint_id.category
                application.variable_schema = application.blueprint_id.variable_schema
                application.application_remark = application.blueprint_id.application_remark

    def _write_internal(self, values):
        return super().write(values)

    def _check_manager(self):
        self.check_access('write')
        if not self.env.user.has_group('qing_sms.group_sms_manager'):
            raise AccessError(_('Only SMS managers can submit template applications.'))

    @api.constrains('account_id', 'sign_id', 'blueprint_id')
    def _check_application_company(self):
        for application in self:
            if application.account_id.company_id != application.company_id or application.sign_id.account_id != application.account_id:
                raise ValidationError(_('Account and sign must belong to the application company.'))
            if application.blueprint_id.company_id and application.blueprint_id.company_id != application.company_id:
                raise ValidationError(_('Blueprint belongs to another company.'))

    account_id = fields.Many2one('qing.sms.account', required=True, check_company=True, ondelete='restrict')
    application_remark = fields.Text()
    apply_scene_content = fields.Text()
    blueprint_id = fields.Many2one('qing.sms.blueprint', ondelete='restrict')
    category = fields.Selection([('notification', 'Notification'), ('otp', 'One-time code')], required=True, default='notification')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    content = fields.Text(required=True)
    error_code = fields.Char(readonly=True)
    last_checked_at = fields.Datetime(readonly=True)
    name = fields.Char(required=True)
    next_check_at = fields.Datetime(readonly=True)
    payload_hash = fields.Char(readonly=True)
    provider_order_id = fields.Char(readonly=True)
    provider_request_id = fields.Char(readonly=True)
    provider_variable_rules = fields.Json(default=dict)
    reject_reason = fields.Text(readonly=True)
    remote_code = fields.Char(readonly=True)
    remote_template_id = fields.Many2one('qing.sms.remote.template', readonly=True, check_company=True, ondelete='restrict')
    request_key = fields.Char(required=True, default=lambda self: uuid4().hex, readonly=True, copy=False)
    sign_id = fields.Many2one('qing.sms.sign', required=True, check_company=True, ondelete='restrict')
    state = fields.Selection(APPLICATION_STATES, required=True, default='draft', readonly=True)
    submitted_at = fields.Datetime(readonly=True)
    submitted_payload = fields.Json(readonly=True, groups='qing_sms.group_sms_manager')
    variable_schema = fields.Json(default=list)

    _request_unique = models.Constraint('unique(request_key)', 'Application request key already exists.')

    def action_queue(self):
        self._check_manager()
        for application in self:
            if application.state != 'draft':
                continue
            schema = application.variable_schema or []
            keys = [item.get('key') for item in schema if isinstance(item, dict)]
            if len(keys) != len(schema) or len(set(keys)) != len(keys) or set(keys) != set(PLACEHOLDER.findall(application.content or '')):
                raise UserError(_('Application variables and content do not match.'))
            if not application.sign_id.active or application.sign_id.audit_state != 'approved':
                raise UserError(_('The sign must be approved before application.'))
            application.account_id._validate_template_application(application)
            payload = {'name': application.name, 'content': application.content,
                       'category': application.category, 'sign': application.sign_id.name,
                       'variable_schema': schema, 'provider_variable_rules': application.provider_variable_rules or {},
                       'application_remark': application.application_remark or '',
                       'apply_scene_content': application.apply_scene_content or ''}
            application._write_internal({'state': 'queued', 'submitted_payload': payload,
                                         'payload_hash': fingerprint(payload), 'submitted_at': fields.Datetime.now()})
        self.env.ref('qing_sms.ir_cron_template_application').sudo()._trigger()
        return True

    def action_clone(self):
        self.ensure_one()
        self._check_manager()
        values = {key: self[key] for key in ('name', 'content', 'category', 'variable_schema',
            'provider_variable_rules', 'application_remark', 'apply_scene_content')}
        values.update({'company_id': self.company_id.id, 'account_id': self.account_id.id,
                       'sign_id': self.sign_id.id, 'blueprint_id': self.blueprint_id.id})
        draft = self.create(values)
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': draft.id, 'view_mode': 'form', 'target': 'current'}

    def action_link_remote(self, remote):
        """Explicitly reconcile a cloud-created template after an unknown outcome."""
        self.ensure_one()
        self._check_manager()
        remote.ensure_one()
        if self.state not in ('unknown', 'pending') or remote.account_id != self.account_id:
            raise UserError(_('The cloud template cannot be linked to this application.'))
        if remote.category != self.category or remote.content != self.content or set(remote.variables or []) != set(PLACEHOLDER.findall(self.content)):
            raise UserError(_('Cloud template content, category or variables do not match.'))
        self._write_internal({'remote_template_id': remote.id, 'remote_code': remote.remote_id,
                              'state': remote.audit_state if remote.audit_state in ('approved', 'rejected') else 'pending'})
        return True

    def action_export_materials(self):
        self.ensure_one()
        self.check_access('read')
        materials = {'name': self.name, 'content': self.content, 'category': self.category,
                     'sign': self.sign_id.name, 'variable_schema': self.variable_schema,
                     'provider_variable_rules': self.provider_variable_rules,
                     'remark': self.application_remark, 'apply_scene_content': self.apply_scene_content}
        attachment = self.env['ir.attachment'].create({'name': f'sms-template-{self.request_key}.json',
            'type': 'binary', 'datas': base64.b64encode(json.dumps(materials, ensure_ascii=False,
                indent=2).encode()), 'mimetype': 'application/json', 'res_model': self._name, 'res_id': self.id})
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{attachment.id}?download=true', 'target': 'self'}

    def action_open_link(self):
        self.ensure_one()
        self._check_manager()
        if self.state not in ('unknown', 'pending'):
            raise UserError(_('Only unresolved applications may be linked to a cloud template.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.sms.application.link',
                'view_mode': 'form', 'target': 'new', 'context': {'default_application_id': self.id}}

    @api.model
    def _process_one(self, application_id):
        """Commit an unknown create intent before calling a provider; never retry Create."""
        with self.env.registry.cursor() as cr:
            cr.execute('SELECT pg_try_advisory_lock(%s, %s)', [APPLICATION_LOCK_NAMESPACE, application_id])
            if not cr.fetchone()[0]:
                return False
            try:
                env = api.Environment(cr, SUPERUSER_ID, {})
                application = env[self._name].browse(application_id).exists()
                if not application or application.state not in ('queued', 'pending', 'unknown'):
                    return False
                account = application.account_id
                if application.state == 'queued':
                    account.lock_for_update()
                    now = fields.Datetime.now()
                    if account.template_apply_not_before and account.template_apply_not_before > now:
                        return False
                    account.write({'template_apply_not_before': now + timedelta(seconds=30)})
                    application._write_internal({'state': 'unknown', 'error_code': 'OUTCOME_UNKNOWN'})
                    cr.commit()
                    try:
                        result = account._create_template_application(application)
                    except Exception:
                        cr.rollback()
                        return True
                else:
                    if not application.remote_code or application.next_check_at and application.next_check_at > fields.Datetime.now():
                        return False
                    try:
                        result = account._query_template_application(application)
                    except Exception:
                        cr.rollback()
                        application.invalidate_recordset()
                        application._write_internal({'next_check_at': fields.Datetime.now() + timedelta(minutes=10),
                                                     'error_code': 'QUERY_FAILED'})
                        cr.commit()
                        return True
                if result.get('state') not in ('pending', 'approved', 'rejected', 'failed', 'unknown'):
                    raise ValueError('Invalid template application result')
                application._write_internal({'state': result['state'], 'remote_code': result.get('remote_code') or application.remote_code,
                    'provider_request_id': result.get('request_id') or application.provider_request_id,
                    'provider_order_id': result.get('order_id') or application.provider_order_id,
                    'error_code': result.get('code'), 'reject_reason': result.get('reject_reason'),
                    'last_checked_at': fields.Datetime.now(),
                    'next_check_at': fields.Datetime.now() + timedelta(minutes=10) if result['state'] in ('pending', 'unknown') else False})
                cr.commit()
                return True
            finally:
                cr.rollback()
                cr.execute('SELECT pg_advisory_unlock(%s, %s)', [APPLICATION_LOCK_NAMESPACE, application_id])
                cr.commit()

    @api.model
    def _cron_process(self, limit=20):
        with self.env.registry.cursor() as cr:
            cr.execute("""SELECT id FROM qing_sms_template_application
                WHERE state = 'queued' OR (state IN ('pending', 'unknown') AND remote_code IS NOT NULL
                AND (next_check_at IS NULL OR next_check_at <= NOW())) ORDER BY id LIMIT %s""", [min(limit, 100)])
            ids = [row[0] for row in cr.fetchall()]
        for application_id in ids:
            self._process_one(application_id)

    @api.model_create_multi
    def create(self, vals_list):
        if any({'state', 'submitted_payload', 'payload_hash', 'remote_code', 'remote_template_id', 'request_key'} & vals.keys() for vals in vals_list):
            raise AccessError(_('Application result fields cannot be set directly.'))
        return super().create(vals_list)

    def write(self, vals):
        if any(record.state != 'draft' for record in self):
            raise AccessError(_('Submitted applications are immutable. Clone a new draft.'))
        if {'state', 'submitted_payload', 'payload_hash', 'request_key', 'remote_code', 'remote_template_id'} & vals.keys():
            raise AccessError(_('Application result fields cannot be edited.'))
        return super().write(vals)
