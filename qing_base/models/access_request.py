import re
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


REQUEST_STATES = [('pending', 'Pending'), ('handled', 'Handled'), ('rejected', 'Rejected')]
REQUEST_OPERATIONS = [('read', 'Read'), ('write', 'Write'), ('create', 'Create'), ('unlink', 'Delete'), ('other', 'Other')]
MODEL_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
MAX_PENDING_REQUESTS_PER_TEN_MINUTES = 10


class QingAccessRequest(models.Model):
    _name = 'qing.access.request'
    _description = 'Permission Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'model_name'

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    explanation = fields.Text(required=True, readonly=True)
    handler_id = fields.Many2one('res.users', readonly=True)
    handler_note = fields.Text(readonly=True)
    model_name = fields.Char(required=True, readonly=True, index=True)
    operation = fields.Selection(REQUEST_OPERATIONS, required=True, readonly=True)
    record_id = fields.Integer(readonly=True)
    requester_id = fields.Many2one('res.users', required=True, readonly=True, index=True)
    state = fields.Selection(REQUEST_STATES, required=True, default='pending', readonly=True, index=True)

    @api.model
    def _configured_handlers(self, company):
        group = self.env.ref('qing_base.group_access_request_handler')
        return group.sudo().all_user_ids.filtered(
            lambda user: user.active and not user.share and company in user.company_ids
        )

    @api.model_create_multi
    def create(self, vals_list):
        if len(vals_list) != 1 or not self.env.user._is_internal():
            raise AccessError(self.env._('Only an internal user may submit one request at a time.'))
        vals = vals_list[0]
        allowed = {'model_name', 'operation', 'record_id', 'explanation'}
        if set(vals) - allowed:
            raise AccessError(self.env._('The requester and handling fields are set by the server.'))
        model_name = vals.get('model_name') or ''
        explanation = (vals.get('explanation') or '').strip()
        operation = vals.get('operation') or 'other'
        record_id = vals.get('record_id') or 0
        if not isinstance(model_name, str) or len(model_name) > 128 or not MODEL_NAME.fullmatch(model_name):
            raise ValidationError(self.env._('Enter a valid model name.'))
        if operation not in dict(REQUEST_OPERATIONS):
            raise ValidationError(self.env._('Enter a valid operation.'))
        if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id < 0:
            raise ValidationError(self.env._('Enter a valid record identifier.'))
        if not explanation or len(explanation) > 2000:
            raise ValidationError(self.env._('The explanation must contain 1 to 2000 characters.'))
        company = self.env.company
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', (2621, self.env.uid))
        if not self.env.cr.fetchone()[0]:
            raise UserError(self.env._('Another request is being submitted. Please try again.'))
        domain = [
            ('requester_id', '=', self.env.uid), ('company_id', '=', company.id),
            ('model_name', '=', model_name), ('operation', '=', operation),
            ('record_id', '=', record_id), ('state', '=', 'pending'),
        ]
        existing = self.search(domain, limit=1)
        if existing:
            return existing
        recent_domain = [('requester_id', '=', self.env.uid),
                         ('create_date', '>=', fields.Datetime.now() - timedelta(minutes=10))]
        if self.search_count(recent_domain, limit=MAX_PENDING_REQUESTS_PER_TEN_MINUTES) >= MAX_PENDING_REQUESTS_PER_TEN_MINUTES:
            raise UserError(self.env._('Too many requests. Please try again later.'))
        handlers = self._configured_handlers(company)
        if not handlers:
            raise UserError(self.env._('No permission request handler is configured for this company. Please contact an administrator.'))
        record = super().create([{
            'requester_id': self.env.uid, 'company_id': company.id, 'model_name': model_name,
            'operation': operation, 'record_id': record_id, 'explanation': explanation, 'state': 'pending',
        }])
        for handler in handlers:
            record.sudo().activity_schedule(
                'mail.mail_activity_data_todo', user_id=handler.id,
                summary=self.env._('Review permission request'),
            )
        return record

    def write(self, vals):
        if not self.env.user.has_group('qing_base.group_access_request_handler'):
            raise AccessError(self.env._('Only an assigned permission request handler may handle requests.'))
        if not vals or set(vals) - {'state', 'handler_note'}:
            raise AccessError(self.env._('Only a handling decision and note can be submitted.'))
        if 'state' in vals and vals['state'] not in ('handled', 'rejected'):
            raise AccessError(self.env._('Select a valid handling decision.'))
        self.check_access('write')
        if any(record.state != 'pending' or record.company_id not in self.env.companies for record in self):
            raise AccessError(self.env._('Only pending requests in an active managed company can be handled.'))
        note = vals.get('handler_note') or ''
        if not isinstance(note, str) or len(note) > 2000:
            raise ValidationError(self.env._('The handling note must not exceed 2000 characters.'))
        update = dict(vals)
        if 'state' not in update:
            return super().write(update)
        update['handler_id'] = self.env.uid
        result = super().write(update)
        for record in self:
            record.sudo().activity_unlink(['mail.mail_activity_data_todo'], only_automated=False)
            record.sudo().message_post(
                body=self.env._('Your permission request has been handled. Open the request for the outcome.'),
                partner_ids=[record.requester_id.partner_id.id], message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
        return result

    def action_mark_handled(self):
        self.write({'state': 'handled'})
        return True

    def action_mark_rejected(self):
        self.write({'state': 'rejected'})
        return True

    def unlink(self):
        raise AccessError(self.env._('Permission requests cannot be deleted manually.'))
