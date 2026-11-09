from datetime import timedelta
from uuid import uuid4

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import AccessError, UserError
from .constants import LOCK_NAMESPACE, MESSAGE_STATES, fingerprint


class SmsMessage(models.Model):
    _name = 'qing.sms.message'
    _description = '国内短信发送记录'
    _rec_name = 'event_key'
    _order = 'id desc'
    _check_company_auto = True

    @api.depends('binding_id.name', 'number')
    def _compute_display_name(self):
        for message in self:
            message.display_name = f'{message.binding_id.name} · {message.number}'

    def _write_internal(self, values):
        return super().write(values)

    @api.model
    def _enqueue(self, binding, number, parameters, body, event_key, record_id=False, sms=None, scheduled_at=False):
        digest = fingerprint([binding.id, number, parameters, record_id, scheduled_at])
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'sms:{binding.company_id.id}:{event_key}'])
        Message = self.sudo()
        domain = [('company_id', '=', binding.company_id.id), ('event_key', '=', event_key)]
        existing = Message.search(domain, limit=1)
        if existing:
            if existing.payload_hash != digest:
                raise UserError(_('同一事件标识不能用于不同的短信内容。'))
            return existing.with_env(self.env)
        otp = binding.remote_template_id.category == 'otp'
        preview = _('验证码内容已隐藏') if otp else body
        sms = sms or self.env['sms.sms'].sudo().create({'number': number, 'body': preview, 'state': 'process', 'domestic_company_id': binding.company_id.id})
        values = {
            'account_id': binding.account_id.id, 'binding_id': binding.id, 'company_id': binding.company_id.id,
            'event_key': event_key, 'payload_hash': digest, 'number': number, 'parameters': parameters,
            'body': preview, 'sms_id': sms.id, 'sms_uuid': sms.uuid, 'template_hash': binding._fingerprint(),
            'template_revision_id': binding.remote_template_id.revision_ids[:1].id,
            'remote_code': binding.remote_template_id.remote_id, 'sign_name': binding.sign_id.name,
            'res_model': binding.template_id.model if record_id else False, 'res_id': record_id,
            'expires_at': fields.Datetime.now() + timedelta(minutes=10) if otp else False,
            'is_sensitive': otp, 'not_before': scheduled_at or fields.Datetime.now(),
        }
        message = super(SmsMessage, Message).create(values)
        sms.write({'domestic_message_id': message.id, 'state': 'process', 'to_delete': False, 'body': preview})
        self.env.ref('qing_sms.ir_cron_dispatch').sudo()._trigger()
        return message.with_env(self.env)

    @api.model
    def _import_legacy(self, values):
        return super(SmsMessage, self.sudo()).create(values)

    def _preflight(self):
        self.ensure_one()
        self.binding_id._check_usable(for_enqueue=False)
        if self.event_id:
            event = self.event_id
            if event.event_code != 'native.template' and event.trigger != 'manual' and event.reason_code != 'EXISTING_MESSAGE_CLAIMED' and (not event.scenario_id.enabled or not event.scenario_id.active):
                raise UserError(_('The SMS scenario is no longer enabled.'))
            sender = event.sender_user_id
            if not sender.active or event.company_id not in sender.company_ids or not sender.has_group('qing_sms.group_sms_user'):
                raise UserError(_('The scenario sender lost SMS permission.'))
            handler_name = self.env['qing.sms.scenario.handler']._get_handlers().get(event.event_code)
            if not handler_name:
                raise UserError(_('The scenario handler is unavailable.'))
            record = self.env[event.res_model].with_user(sender).with_company(event.company_id).browse(event.res_id).exists()
            if not record:
                raise UserError(_('The business record is unavailable.'))
            record.check_access('read')
            self.env[handler_name].with_user(sender)._check_before_send(record, event)
        if self.company_id != self.account_id.company_id or self.company_id != self.binding_id.company_id:
            raise UserError(_('短信账户所属公司已变更。'))
        if self.template_hash != self.binding_id._fingerprint():
            raise UserError(_('排队后模板已变更，请重新创建短信任务。'))
        if self.expires_at and self.expires_at < fields.Datetime.now():
            raise UserError(_('验证码任务已过期。'))

    def _apply_result(self, result, kind):
        self.ensure_one()
        state = result.get('state')
        allowed = {'accepted', 'delivered', 'failed', 'unknown'} if kind in ('submit', 'query') else {'canceled'}
        if state not in allowed:
            raise ValueError('Invalid provider result')
        # Receipt polling is monotonic; repeated/out-of-order results cannot downgrade terminal states.
        if self.state in ('delivered', 'failed', 'canceled') and kind == 'query':
            return
        values = {'state': state, 'error_code': result.get('code'),
                  'next_query_at': fields.Datetime.now() + timedelta(minutes=5),
                  'query_count': self.query_count + (kind == 'query')}
        for key in ('provider_id', 'request_id'):
            if result.get(key):
                values[key] = result[key]
        if state in ('delivered', 'failed', 'canceled') or self.is_sensitive and state == 'accepted':
            values['parameters'] = {}
        self._write_internal(values)
        self.env['qing.sms.attempt'].sudo().create({
            'message_id': self.id, 'kind': kind, 'state': state,
            'code': result.get('code'), 'request_id': result.get('request_id')})
        sms = self.sms_id.sudo()
        if sms:
            native = {'queued': 'process', 'unknown': 'process', 'accepted': 'pending',
                      'delivered': 'sent', 'failed': 'error', 'canceled': 'canceled'}[state]
            sms._update_sms_state_and_trackers(native, failure_type='sms_server' if state == 'failed' else False)
            if state == 'canceled':
                # Native tracker refuses process -> canceled, but domestic queued intent
                # has not reached any provider and remains safely retractable.
                notifications = self.env['mail.notification'].sudo().search([('sms_id_int', '=', sms.id)])
                notifications.write({'notification_status': 'canceled'})
            sms._domestic_result_hook(result)
            sms.mail_message_id._notify_message_notification_update()

    @api.model
    def _process_one(self, message_id):
        """Own cursor + session lock: commit intent before network; never commit callers' work."""
        with self.env.registry.cursor() as cr:
            cr.execute('SELECT pg_try_advisory_lock(%s, %s)', [LOCK_NAMESPACE, message_id])
            if not cr.fetchone()[0]:
                return False
            try:
                env = api.Environment(cr, SUPERUSER_ID, {})
                message = env[self._name].browse(message_id).exists()
                if not message or message.state not in ('queued', 'unknown', 'accepted'):
                    return False
                message = message.with_company(message.company_id)
                kind = 'submit' if message.state == 'queued' else 'query'
                if kind == 'submit' and message.not_before and message.not_before > fields.Datetime.now():
                    return False
                if kind == 'submit':
                    try:
                        message._preflight()
                    except (UserError, AccessError) as error:
                        message._apply_result({'state': 'failed', 'code': 'PREFLIGHT_FAILED'}, kind)
                        missing_handler = message.event_id and message.event_id.event_code not in env['qing.sms.scenario.handler']._get_handlers()
                        message._write_internal({'preflight_reason_code': 'SCENARIO_UNAVAILABLE' if missing_handler else 'SENDER_ACCESS_REVOKED' if isinstance(error, AccessError) else 'PREFLIGHT_BLOCKED',
                                                 'preflight_reason_message': str(error)[:250]})
                        cr.commit()
                        return False
                    message._write_internal({'state': 'unknown', 'submitted_at': fields.Datetime.now(),
                                             'error_code': 'OUTCOME_UNKNOWN'})
                    env['qing.sms.attempt'].create({'message_id': message.id, 'kind': kind, 'state': 'unknown'})
                    cr.commit()
                try:
                    adapter = message.account_id._get_adapter()
                    result = adapter.send(message) if kind == 'submit' else adapter.query(message)
                    message._apply_result(result, kind)
                    cr.commit()
                except Exception:
                    cr.rollback()
                    message.invalidate_recordset()
                    # A paid send may have happened. Never transition back to queued here.
                    message._write_internal({'error_code': 'OUTCOME_UNKNOWN' if kind == 'submit' else 'QUERY_FAILED',
                                             'next_query_at': fields.Datetime.now() + timedelta(minutes=5),
                                             'query_count': message.query_count + (kind == 'query')})
                    cr.commit()
                return True
            finally:
                cr.rollback()
                cr.execute('SELECT pg_advisory_unlock(%s, %s)', [LOCK_NAMESPACE, message_id])
                cr.commit()

    @api.model
    def _cron_dispatch(self, limit=20):
        domain = [('is_sensitive', '=', True), ('expires_at', '<', fields.Datetime.now())]
        expired = self.sudo().search(domain)
        expired._write_internal({'parameters': {}})
        # Fresh discovery only sees committed intents. Bounded polling; unknown without a receipt stays unknown.
        with self.env.registry.cursor() as cr:
            cr.execute("""SELECT id FROM qing_sms_message WHERE (state = 'queued' AND (not_before IS NULL OR not_before <= NOW()))
                OR (state IN ('unknown', 'accepted') AND query_count < 288
                    AND (next_query_at IS NULL OR next_query_at <= NOW())) ORDER BY id LIMIT %s""", [min(limit, 100)])
            ids = [row[0] for row in cr.fetchall()]
        for message_id in ids:
            try:
                self._process_one(message_id)
            except Exception:
                # Durable intent survives; another task must not be starved by this task's DB failure.
                continue

    account_id = fields.Many2one('qing.sms.account', string='短信账户', required=True, ondelete='restrict', check_company=True)
    attempt_ids = fields.One2many('qing.sms.attempt', 'message_id', string='处理记录')
    binding_id = fields.Many2one('qing.sms.binding', string='业务模板', required=True, ondelete='restrict', check_company=True)
    body = fields.Text(string='正文预览', readonly=True)
    company_id = fields.Many2one('res.company', string='公司', required=True, index=True)
    error_code = fields.Char(string='结果代码', readonly=True)
    event_id = fields.Many2one('qing.sms.event', readonly=True, ondelete='set null', check_company=True)
    event_key = fields.Char(string='业务事件标识', required=True, readonly=True)
    expires_at = fields.Datetime(readonly=True)
    is_sensitive = fields.Boolean(readonly=True)
    next_query_at = fields.Datetime(readonly=True)
    not_before = fields.Datetime(string='计划发送时间', readonly=True, index=True)
    number = fields.Char(string='手机号码', required=True, readonly=True)
    parameters = fields.Json(readonly=True, groups='base.group_system')
    payload_hash = fields.Char(required=True, readonly=True)
    preflight_reason_code = fields.Char(readonly=True)
    preflight_reason_message = fields.Char(readonly=True)
    provider_id = fields.Char(string='厂商流水号', readonly=True, index=True)
    query_count = fields.Integer(readonly=True)
    remote_code = fields.Char(readonly=True)
    request_id = fields.Char(string='请求编号', readonly=True)
    res_id = fields.Many2oneReference(string='业务单据', model_field='res_model', readonly=True)
    res_model = fields.Char(readonly=True)
    sign_name = fields.Char(readonly=True)
    sms_id = fields.Many2one('sms.sms', readonly=True, ondelete='set null')
    sms_uuid = fields.Char(readonly=True, index=True)
    state = fields.Selection(MESSAGE_STATES, default='queued', required=True, readonly=True, index=True, string='Message Status')
    submitted_at = fields.Datetime(string='提交时间', readonly=True)
    template_hash = fields.Char(readonly=True)
    template_revision_id = fields.Many2one('qing.sms.template.revision', readonly=True, ondelete='restrict')

    _event_unique = models.Constraint('unique(company_id, event_key)', '短信业务事件不能重复。')

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_('请通过业务模板发送短信。'))

    def write(self, values):
        raise AccessError(_('发送记录不可直接修改。'))

    def unlink(self):
        raise AccessError(_('发送记录不可直接删除。'))

    def action_cancel(self):
        self.check_access('read')
        for message in self:
            # Same lock as the worker, including its network phase.
            self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', [LOCK_NAMESPACE, message.id])
            if not self.env.cr.fetchone()[0]:
                raise UserError(_('任务正在处理，请稍后查询状态。'))
            message.invalidate_recordset()
            if message.state != 'queued':
                raise UserError(_('只能取消尚未提交的短信。'))
            message.sudo()._apply_result({'state': 'canceled', 'code': 'USER_CANCELED'}, 'cancel')
        return True

    def action_query(self):
        self.check_access('read')
        for message in self:
            if message.state in ('unknown', 'accepted'):
                message.sudo()._write_internal({'next_query_at': fields.Datetime.now(), 'query_count': 0})
        self.env.ref('qing_sms.ir_cron_dispatch').sudo()._trigger()
        return True
