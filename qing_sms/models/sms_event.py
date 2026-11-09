from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError, UserError
from uuid import uuid4
from psycopg2.errors import UniqueViolation


EVENT_STATES = [
    ('pending', 'Pending'), ('blocked', 'Blocked'), ('enqueued', 'Enqueued'),
    ('skipped', 'Skipped'), ('canceled', 'Canceled'),
]


class SmsEvent(models.Model):
    _name = 'qing.sms.event'
    _description = 'Domestic SMS business event'
    _check_company_auto = True
    _order = 'id desc'

    @api.model
    def _summary_for_record(self, record, event_key):
        """Expose only a state label after checking the caller's source-record access."""
        record.ensure_one()
        record.check_access('read')
        company = record.company_id if 'company_id' in record._fields and record.company_id else self.env.company
        event = self.sudo().search([('company_id', '=', company.id), ('event_key', '=', event_key)], limit=1)
        if not event:
            return _('No SMS event')
        state = event.message_ids[:1].state if event.message_ids else event.state
        labels = {'pending': _('Waiting for preparation'), 'blocked': _('Blocked'),
            'enqueued': _('Enqueued'), 'skipped': _('Skipped'), 'queued': _('Queued'),
            'unknown': _('Outcome unknown'), 'accepted': _('Accepted'),
            'delivered': _('Delivered'), 'failed': _('Failed'), 'canceled': _('Canceled')}
        return labels.get(state, _('Unknown SMS event.'))

    def _create_captured(self, values):
        """The losing REPEATABLE READ cursor may not see the winner after waiting."""
        try:
            with self.env.cr.savepoint():
                return super(SmsEvent, self.sudo()).create(values)
        except UniqueViolation as error:
            constraint = error.diag.constraint_name
            if constraint not in ('qing_sms_event_company_key_unique', 'qing_sms_event_native_mail_unique'):
                raise
            with self.env.registry.cursor() as cursor:
                if constraint == 'qing_sms_event_native_mail_unique':
                    cursor.execute('SELECT id FROM qing_sms_event WHERE mail_message_id=%s AND event_code=%s',
                                   [values['mail_message_id'], values['event_code']])
                else:
                    cursor.execute('SELECT id FROM qing_sms_event WHERE company_id=%s AND event_key=%s',
                                   [values['company_id'], values['event_key']])
                existing = cursor.fetchone()
            if not existing:
                raise
            return self.sudo().browse(existing[0])

    @api.model
    def _capture_native(self, record, message, template, binding, sender, targets, values,
                        not_before=False, preparation_error=False):
        """Freeze a native notification before a scheduled mail cron changes identity."""
        record.ensure_one()
        company = record.company_id if 'company_id' in record._fields and record.company_id else self.env.company
        if message.model != record._name or message.res_id != record.id or message.create_uid != sender:
            raise AccessError(_('Native SMS message identity does not match its source.'))
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'sms-native-mail:{message.id}'])
        existing = self.sudo().search([('event_code', '=', 'native.template'),
                                       ('mail_message_id', '=', message.id)], limit=1)
        if existing:
            return existing
        state, reason = 'pending', preparation_error
        try:
            if not sender.active or company not in sender.company_ids or not sender.has_group('qing_sms.group_sms_user'):
                raise AccessError(_('The native SMS sender has no company or SMS permission.'))
            record.with_user(sender).with_company(company).check_access('read')
            if not binding or binding.company_id != company or binding.template_id != template:
                raise UserError(_('Domestic SMS template binding is unavailable.'))
            if binding.remote_template_id.category == 'otp':
                raise UserError(_('One-time codes require the dedicated secure SMS flow.'))
            binding.with_user(sender).with_company(company)._check_usable()
        except (AccessError, UserError) as error:
            reason = str(error)[:250]
        if reason:
            state = 'blocked'
        event_uuid = str(uuid4())
        return self._create_captured({
            'company_id': company.id, 'event_code': 'native.template',
            'event_key': f'native.template:{event_uuid}', 'res_model': record._name,
            'res_id': record.id, 'mail_message_id': message.id,
            'native_template_id': template.id, 'binding_id': binding.id if binding else False,
            'binding_frozen_at': fields.Datetime.now(), 'sender_user_id': sender.id,
            'actor_user_id': self.env.user.id, 'occurred_at': fields.Datetime.now(),
            'not_before': not_before, 'recipient_snapshot': targets,
            'value_snapshot': values, 'state': state,
            'trigger': 'native',
            'reason_code': 'NATIVE_CONFIGURATION_BLOCKED' if reason else False,
            'reason_message': reason,
        })

    @api.model
    def _capture_manual(self, event_code, record, event_key, binding, recipient, values):
        """Explicit user action may use a reviewed binding without enabling automation."""
        record.ensure_one()
        binding.ensure_one()
        company = record.company_id
        user = self.env.user
        if self.env.ref('qing_sms.group_sms_user') not in user.group_ids or company not in user.company_ids:
            raise AccessError(_('SMS send permission is required.'))
        record.check_access('read')
        binding._check_usable()
        if binding.company_id != company:
            raise UserError(_('The binding belongs to another company or model.'))
        handler_name = self.env['qing.sms.scenario.handler']._get_handlers().get(event_code)
        if not handler_name or self.env[handler_name]._record_model != record._name:
            raise UserError(_('The scenario handler is unavailable.'))
        applicable, reason = self.env[handler_name]._is_applicable(record)
        if not applicable:
            raise UserError(_('The business event is not eligible: %s', reason))
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'sms-event:{company.id}:{event_key}'])
        existing = self.sudo().search([('company_id', '=', company.id), ('event_key', '=', event_key)], limit=1)
        if existing:
            return existing
        return self._create_captured({'company_id': company.id, 'event_code': event_code,
            'event_key': event_key, 'res_model': record._name, 'res_id': record.id,
            'binding_id': binding.id, 'binding_frozen_at': fields.Datetime.now(),
            'sender_user_id': user.id, 'actor_user_id': user.id,
            'occurred_at': fields.Datetime.now(), 'state': 'pending', 'trigger': 'manual',
            'recipient_snapshot': recipient, 'value_snapshot': values})

    @api.model
    def _capture(self, event_code, record, event_key):
        """Capture one business transition in the caller transaction, without network I/O."""
        record.ensure_one()
        handlers = self.env['qing.sms.scenario.handler']._get_handlers()
        handler_name = handlers.get(event_code)
        if not handler_name or not event_key or len(event_key) > 200:
            raise UserError(_('Unknown SMS event.'))
        handler = self.env[handler_name]
        if handler._record_model != record._name:
            raise UserError(_('The SMS event does not match the business record.'))
        company = record.company_id
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'sms-event:{company.id}:{event_key}'])
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'sms-scenario:{company.id}:{event_code}'])
        domain = [('company_id', '=', company.id), ('event_key', '=', event_key)]
        existing = self.sudo().search(domain, limit=1)
        if existing:
            return existing
        scenario = self.env['qing.sms.scenario'].sudo().search([
            ('company_id', '=', company.id), ('event_code', '=', event_code), ('active', '=', True), ('enabled', '=', True)], limit=1)
        if not scenario:
            return self.browse()
        sender = scenario.sender_user_id
        values = {'company_id': company.id, 'event_code': event_code, 'event_key': event_key,
                  'res_model': record._name, 'res_id': record.id, 'scenario_id': scenario.id,
                  'actor_user_id': self.env.user.id, 'sender_user_id': sender.id,
                  'occurred_at': fields.Datetime.now(), 'config_revision': scenario.config_revision,
                  'state': 'pending'}
        legacy = self.env['qing.sms.message'].sudo().search(domain, limit=1)
        if legacy:
            values.update({'state': 'enqueued', 'binding_id': legacy.binding_id.id,
                'binding_frozen_at': legacy.create_date, 'recipient_snapshot': {'number': legacy.number},
                'reason_code': 'EXISTING_MESSAGE_CLAIMED'})
            event = self._create_captured(values)
            legacy._write_internal({'event_id': event.id})
            return event
        try:
            if not sender or not sender.active or company not in sender.company_ids or not sender.has_group('qing_sms.group_sms_user'):
                raise AccessError(_('The scenario sender has no company or SMS permission.'))
            source = record.with_user(sender).with_company(company)
            source.check_access('read')
            applicable, reason = handler.with_user(sender)._is_applicable(source)
            if not applicable:
                values.update(state='skipped', reason_code=reason or 'NOT_APPLICABLE')
                return self._create_captured(values)
            recipient = handler.with_user(sender)._get_recipient(source, scenario)
            semantic = handler.with_user(sender)._prepare_values(source, scenario)
            variant = handler.with_user(sender)._select_variant(source, scenario)
            binding = scenario.binding_ids.filtered(lambda row: row.variant_code == variant and row.accept_new)[:1]
            if not binding:
                raise UserError(_('No active binding exists for this scenario variant.'))
            binding.with_user(sender).with_company(company)._check_usable()
            values.update(binding_id=binding.id, binding_frozen_at=fields.Datetime.now(),
                          blueprint_code=binding.blueprint_id.code, blueprint_version=binding.blueprint_id.version,
                          recipient_snapshot=recipient, value_snapshot=semantic)
            if not isinstance(recipient, dict) or not recipient.get('number') or not isinstance(semantic, dict):
                raise UserError(_('The recipient or template values are missing.'))
        except (AccessError, UserError) as error:
            values.update(state='blocked', reason_code='CONFIGURATION_BLOCKED',
                          reason_message=str(error)[:250])
        return self._create_captured(values)

    def _write_internal(self, values):
        return super().write(values)

    @api.model
    def _cron_prepare(self, limit=50):
        """Prepare committed events in a separate cron transaction; no provider calls."""
        domain = [('state', '=', 'pending'), ('event_code', '!=', 'native.template')]
        for event in self.sudo().search(domain, order='id', limit=min(limit, 100)):
            self.env.cr.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))',
                                [f'sms-event-prepare:{event.id}'])
            if not self.env.cr.fetchone()[0]:
                continue
            event.invalidate_recordset()
            if event.state != 'pending':
                continue
            try:
                with self.env.cr.savepoint():
                    event._prepare_one()
            except (AccessError, UserError) as error:
                event._write_internal({'state': 'blocked', 'reason_code': 'PREPARE_BLOCKED',
                                       'reason_message': str(error)[:250]})

    def _prepare_one(self):
        self.ensure_one()
        if self.state != 'pending':
            return self.message_ids[:1]
        if self.trigger == 'manual':
            self._write_internal({'state': 'blocked', 'reason_code': 'MANUAL_INTENT_INCOMPLETE'})
            return self.env['qing.sms.message']
        if self.event_code not in self.env['qing.sms.scenario.handler']._get_handlers():
            self._write_internal({'state': 'blocked', 'reason_code': 'SCENARIO_UNAVAILABLE',
                                  'reason_message': _('The scenario handler is unavailable.')})
            return self.env['qing.sms.message']
        if not self.scenario_id.enabled or not self.scenario_id.active:
            raise UserError(_('The scenario is no longer enabled.'))
        if not self.binding_id or not self.sender_user_id:
            raise UserError(_('The event has no captured template or sender.'))
        message = self.binding_id._send_for_event(self)
        message.sudo()._write_internal({'event_id': self.id})
        if self.mail_message_id:
            sms = message.sms_id.sudo()
            recipient = self.recipient_snapshot or {}
            partner_id = recipient.get('partner_id') if isinstance(recipient, dict) else False
            if not sms.mail_message_id:
                sms.write({'mail_message_id': self.mail_message_id.id, 'partner_id': partner_id})
            notification = self.env['mail.notification'].sudo().search([
                ('mail_message_id', '=', self.mail_message_id.id), ('sms_id_int', '=', sms.id)], limit=1)
            if not notification:
                self.env['mail.notification'].sudo().create({
                    'author_id': self.mail_message_id.author_id.id,
                    'mail_message_id': self.mail_message_id.id,
                    'res_partner_id': partner_id, 'sms_number': sms.number,
                    'notification_type': 'sms', 'sms_id_int': sms.id,
                    'sms_tracker_ids': [Command.create({'sms_uuid': sms.uuid})],
                    'is_read': True, 'notification_status': 'ready'})
                sms._update_sms_state_and_trackers('process')
        self._write_internal({'state': 'enqueued', 'reason_code': False, 'reason_message': False})
        return message

    def action_recheck(self):
        self.check_access('read')
        if not self.env.user.has_group('qing_sms.group_sms_manager'):
            raise AccessError(_('Only SMS managers can recheck blocked events.'))
        for event in self:
            if event.state != 'blocked' or event.message_ids:
                raise UserError(_('Only unsent blocked events may be rechecked.'))
            event.sudo()._refresh_snapshot(self.env.user.id)
        self.env.ref('qing_sms.ir_cron_prepare_events').sudo()._trigger()
        return True

    def _refresh_snapshot(self, resolver_user_id):
        """Rebuild only an unsent blocked event after explicit manager review."""
        self.ensure_one()
        scenario = self.scenario_id
        sender = scenario.sender_user_id
        if not scenario.enabled or not sender or not sender.active or not sender.has_group('qing_sms.group_sms_user'):
            raise UserError(_('The scenario or sender is unavailable.'))
        name = self.env['qing.sms.scenario.handler']._get_handlers().get(self.event_code)
        if not name:
            raise UserError(_('The scenario handler is unavailable.'))
        record = self.env[self.res_model].with_user(sender).with_company(self.company_id).browse(self.res_id).exists()
        if not record:
            raise UserError(_('The source record no longer exists.'))
        record.check_access('read')
        handler = self.env[name].with_user(sender)
        applicable, reason = handler._is_applicable(record)
        if not applicable:
            raise UserError(_('The business event is not eligible: %s', reason))
        recipient = handler._get_recipient(record, scenario)
        semantic = handler._prepare_values(record, scenario)
        variant = handler._select_variant(record, scenario)
        binding = scenario.binding_ids.filtered(lambda row: row.variant_code == variant and row.accept_new)[:1]
        if not binding:
            raise UserError(_('The scenario variant has no binding.'))
        binding.with_user(sender).with_company(self.company_id)._check_usable()
        if not recipient.get('number'):
            raise UserError(_('The recipient has no mobile number.'))
        self._write_internal({'state': 'pending', 'reason_code': False, 'reason_message': False,
            'sender_user_id': sender.id, 'binding_id': binding.id, 'binding_frozen_at': fields.Datetime.now(),
            'recipient_snapshot': recipient, 'value_snapshot': semantic,
            'blueprint_code': binding.blueprint_id.code, 'blueprint_version': binding.blueprint_id.version,
            'config_revision': scenario.config_revision, 'resolution_user_id': resolver_user_id})

    actor_user_id = fields.Many2one('res.users', readonly=True)
    binding_frozen_at = fields.Datetime(readonly=True)
    binding_id = fields.Many2one('qing.sms.binding', readonly=True, check_company=True, ondelete='restrict')
    blueprint_code = fields.Char(readonly=True)
    blueprint_version = fields.Integer(readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    config_revision = fields.Integer(readonly=True)
    event_code = fields.Char(required=True, readonly=True)
    event_key = fields.Char(required=True, readonly=True)
    mail_message_id = fields.Many2one('mail.message', readonly=True, ondelete='set null')
    message_ids = fields.One2many('qing.sms.message', 'event_id', readonly=True)
    native_template_id = fields.Many2one('sms.template', readonly=True, ondelete='set null')
    not_before = fields.Datetime(readonly=True)
    occurred_at = fields.Datetime(readonly=True)
    reason_code = fields.Char(readonly=True)
    reason_message = fields.Char(readonly=True)
    recipient_snapshot = fields.Json(readonly=True, groups='qing_sms.group_sms_manager')
    res_id = fields.Many2oneReference(model_field='res_model', readonly=True)
    res_model = fields.Char(readonly=True)
    retry_after = fields.Datetime(readonly=True)
    resolution_user_id = fields.Many2one('res.users', readonly=True)
    scenario_id = fields.Many2one('qing.sms.scenario', readonly=True, check_company=True, ondelete='restrict')
    sender_user_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection(EVENT_STATES, required=True, default='pending', readonly=True)
    trigger = fields.Selection([('automatic', 'Automatic'), ('manual', 'Manual'),
                                ('native', 'Native')], readonly=True, default='automatic')
    value_snapshot = fields.Json(readonly=True, groups='qing_sms.group_sms_manager')

    _company_key_unique = models.Constraint('unique(company_id, event_key)', 'Domestic SMS business event already exists.')
    _native_mail_unique = models.Constraint('unique(mail_message_id, event_code)', 'Native SMS message event already exists.')

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_('SMS events are created by registered business handlers.'))

    def write(self, vals):
        raise AccessError(_('SMS event facts cannot be edited directly.'))

    def unlink(self):
        raise AccessError(_('SMS event history cannot be deleted.'))
