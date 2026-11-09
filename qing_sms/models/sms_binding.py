import math
import re
from string import Template
from uuid import uuid4

import phonenumbers
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from .constants import fingerprint

SCALAR_TYPES = ('char', 'text', 'selection', 'integer', 'float', 'monetary', 'date', 'datetime')


class SmsBinding(models.Model):
    _name = 'qing.sms.binding'
    _description = '短信业务模板绑定'
    _check_company_auto = True

    def _fingerprint(self):
        self.ensure_one()
        if not self.remote_template_id or not self.sign_id:
            return False
        return fingerprint([self.remote_template_id._fingerprint(), self.sign_id._fingerprint(),
                            self.account_id.company_id.id, self.account_id.provider, self.account_id.sudo().key,
                            self.template_id.id, self.account_id.id,
                            [(v.remote_name, v.key, v.source, v.constant, v.field_path, v.max_length)
                             for v in self.variable_ids]])

    def _check_usable(self, for_enqueue=True):
        self.ensure_one()
        self.check_access('read')
        if not self.env.su and self.company_id not in self.env.companies:
            raise AccessError(_('无权使用其他公司的短信配置。'))
        if not self.active or not self.account_id.active or not self.account_id.enabled:
            raise UserError(_('短信账户或业务绑定未启用。'))
        if for_enqueue and not self.accept_new:
            raise UserError(_('This binding no longer accepts new SMS events.'))
        remote = self.remote_template_id
        if not remote.active or remote.audit_state != 'approved' or not self.sign_id.active or self.sign_id.audit_state != 'approved':
            raise UserError(_('签名和模板均须审核通过。'))
        if remote.category in ('marketing', 'international'):
            raise UserError(_('当前模块支持国内通知和验证码，请使用对应类别的模板。'))
        if remote.sign_names and self.sign_id.name not in remote.sign_names:
            raise UserError(_('当前签名不在模板允许的签名范围内。'))
        if set(self.variable_ids.mapped('remote_name')) != set(remote.variables or []):
            raise UserError(_('请完整配置云端模板的变量映射。'))
        if self.review_hash != self._fingerprint():
            raise UserError(_('模板、签名或变量已变更，请重新核验业务绑定。'))

    def _read_field(self, record, path):
        if not record or not path:
            raise UserError(_('字段映射需要有效业务记录和字段路径。'))
        parts = path.split('.')
        if len(parts) > 4:
            raise UserError(_('字段路径最多四层。'))
        for index, name in enumerate(parts):
            if name.startswith('_') or name not in record._fields:
                raise UserError(_('字段路径无效。'))
            field = record._fields[name]
            record.check_access('read')
            record.check_field_access(field, 'read')
            if index < len(parts) - 1:
                if field.type != 'many2one':
                    raise UserError(_('中间字段必须为单条关联记录。'))
                record = record[name]
                if not record:
                    return ''
            elif field.type in SCALAR_TYPES:
                return record[name]
            else:
                raise UserError(_('仅支持文本、数值和日期字段。'))

    def _prepare_payload(self, number, parameters, record_id=False, for_enqueue=True):
        self._check_usable(for_enqueue=for_enqueue)
        if not isinstance(parameters, dict):
            raise UserError(_('短信参数必须为对象。'))
        try:
            parsed = phonenumbers.parse(number or '', 'CN')
            if parsed.country_code != 86 or not phonenumbers.is_valid_number(parsed) or phonenumbers.number_type(parsed) != phonenumbers.PhoneNumberType.MOBILE:
                raise ValueError()
            number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except (ValueError, phonenumbers.NumberParseException):
            raise UserError(_('请输入有效的中国大陆手机号码。')) from None
        record = self.env[self.template_id.model].browse(record_id).exists() if record_id else None
        if record_id:
            if not record:
                raise UserError(_('业务记录不存在。'))
            record.check_access('read')
            if 'company_id' in record and record.company_id and record.company_id != self.company_id:
                raise UserError(_('业务记录与短信账户公司不一致。'))
        expected = {v.key for v in self.variable_ids if v.source == 'input'}
        if set(parameters) != expected:
            raise UserError(_('调用参数与业务变量不一致。'))
        values = {}
        for variable in self.variable_ids:
            value = parameters.get(variable.key) if variable.source == 'input' else variable.constant
            if variable.source == 'field':
                value = self._read_field(record, variable.field_path)
            if value is None or isinstance(value, (dict, list, bool)) or (isinstance(value, float) and not math.isfinite(value)):
                raise UserError(_('短信变量值无效。'))
            value = str(value)
            if not value or not 0 < len(value) <= variable.max_length or re.search(r'[\r\n\x00-\x1f]', value):
                raise UserError(_('短信变量为空、过长或含控制字符。'))
            values[variable.remote_name] = value
        self.account_id._get_adapter().validate_parameters(self.remote_template_id, values)
        try:
            body = Template(self.remote_template_id.normalized_content or '').substitute(values)
        except (KeyError, ValueError):
            raise UserError(_('云端模板的占位符不能正确渲染。')) from None
        if not body:
            raise UserError(_('模板正文不能为空。'))
        return number, values, body

    @api.depends('review_hash', 'remote_template_id.content', 'remote_template_id.audit_state',
                 'remote_template_id.variables', 'remote_template_id.variable_rules', 'remote_template_id.sign_names',
                 'remote_template_id.active', 'remote_template_id.category', 'remote_template_id.normalized_content',
                 'sign_id.audit_state', 'sign_id.name', 'sign_id.active', 'template_id', 'variable_ids',
                 'variable_ids.remote_name', 'variable_ids.key', 'variable_ids.source', 'variable_ids.constant',
                 'variable_ids.field_path', 'variable_ids.max_length')
    def _compute_needs_review(self):
        for binding in self:
            binding.needs_review = binding.review_hash != binding._fingerprint()

    @api.constrains('sign_id', 'remote_template_id', 'account_id')
    def _check_account(self):
        for binding in self:
            if binding.sign_id.account_id != binding.account_id or binding.remote_template_id.account_id != binding.account_id:
                raise ValidationError(_('签名、模板必须属于同一短信账户。'))

    @api.constrains('blueprint_id', 'scenario_id', 'account_id')
    def _check_scenario_company(self):
        for binding in self:
            if binding.blueprint_id.company_id and binding.blueprint_id.company_id != binding.company_id:
                raise ValidationError(_('The blueprint belongs to another company.'))
            if binding.scenario_id and binding.scenario_id.company_id != binding.company_id:
                raise ValidationError(_('The scenario belongs to another company.'))

    account_id = fields.Many2one('qing.sms.account', string='短信账户', required=True, check_company=True, ondelete='restrict')
    active = fields.Boolean(string='启用', default=True)
    accept_new = fields.Boolean(default=True, readonly=True)
    blueprint_id = fields.Many2one('qing.sms.blueprint', ondelete='restrict')
    company_id = fields.Many2one(string='公司', related='account_id.company_id', store=True, index=True)
    name = fields.Char(string='业务场景', required=True)
    needs_review = fields.Boolean(string='需复核', compute='_compute_needs_review')
    remote_template_id = fields.Many2one('qing.sms.remote.template', string='云端模板', required=True, check_company=True, ondelete='restrict')
    review_hash = fields.Char(readonly=True, copy=False)
    retired_at = fields.Datetime(readonly=True)
    scenario_id = fields.Many2one('qing.sms.scenario', ondelete='set null', check_company=True)
    sign_id = fields.Many2one('qing.sms.sign', string='签名', required=True, check_company=True, ondelete='restrict')
    template_id = fields.Many2one('sms.template', string='Odoo 业务模板', required=True, ondelete='restrict')
    variable_ids = fields.One2many('qing.sms.variable', 'binding_id', string='变量映射', copy=True)
    variant_code = fields.Char(default='default', required=True)

    _scenario_variant_unique = models.Constraint('unique(scenario_id, variant_code)', 'Only one current binding is allowed for each scenario variant.')

    def _write_internal(self, values):
        return super().write(values)

    def _send_for_event(self, event):
        """Enqueue a captured intent, including a version retired after capture."""
        self.ensure_one()
        event.ensure_one()
        if event.binding_id != self or event.company_id != self.company_id or event.message_ids:
            raise UserError(_('The captured SMS binding cannot be reused.'))
        if self.retired_at and (not event.binding_frozen_at or event.binding_frozen_at > self.retired_at):
            raise UserError(_('The binding was retired before this event was captured.'))
        sender = event.sender_user_id
        if not sender.active or self.company_id not in sender.company_ids or not sender.has_group('qing_sms.group_sms_user'):
            raise AccessError(_('The configured sender no longer has SMS permission.'))
        binding = self.with_user(sender).with_company(self.company_id)
        expected = {variable.key for variable in binding.variable_ids if variable.source == 'input'}
        semantic = event.value_snapshot or {}
        if not expected <= semantic.keys():
            raise UserError(_('Captured business values do not cover the template mapping.'))
        parameters = {key: semantic[key] for key in expected}
        number, values, body = binding._prepare_payload(event.recipient_snapshot['number'],
            parameters, event.res_id, for_enqueue=False)
        return binding.env['qing.sms.message']._enqueue(binding, number, values, body, event.event_key,
                                                        event.res_id, scheduled_at=event.not_before)

    def _send_native_scheduled(self, mail_message, number, parameters, event_key, record_id):
        """Honor a persisted native schedule frozen before this binding retired."""
        self.ensure_one()
        schedule = self.env['mail.message.schedule'].sudo().search([
            ('mail_message_id', '=', mail_message.id)], limit=1)
        created_at = mail_message.create_date.replace(microsecond=0) if mail_message.create_date else False
        retired_at = self.retired_at.replace(microsecond=0) if self.retired_at else False
        if not schedule or not retired_at or not created_at or created_at > retired_at:
            raise UserError(_('The native SMS schedule cannot use this retired binding.'))
        sender = mail_message.create_uid
        if not sender.active or not sender.has_group('qing_sms.group_sms_user') or self.company_id not in sender.company_ids:
            raise AccessError(_('The scheduled sender no longer has SMS permission.'))
        binding = self.with_user(sender).with_company(self.company_id)
        number, values, body = binding._prepare_payload(number, parameters, record_id, for_enqueue=False)
        return binding.env['qing.sms.message']._enqueue(binding, number, values, body, event_key, record_id)

    def action_prepare_variables(self):
        self.check_access('write')
        for binding in self:
            existing = set(binding.variable_ids.mapped('remote_name'))
            for index, key in enumerate(binding.remote_template_id.variables or []):
                if key not in existing:
                    self.env['qing.sms.variable'].create({'binding_id': binding.id, 'remote_name': key,
                                                         'key': key, 'sequence': index})
        return True

    def action_review(self):
        self.check_access('write')
        for binding in self:
            old_hash = binding.review_hash
            super(SmsBinding, binding).write({'review_hash': binding._fingerprint()})
            try:
                binding._check_usable()
            except Exception:
                super(SmsBinding, binding).write({'review_hash': old_hash})
                raise
        return True

    def action_open_send(self):
        self._check_usable()
        return {'type': 'ir.actions.act_window', 'name': _('发送短信'), 'res_model': 'qing.sms.send',
                'view_mode': 'form', 'target': 'new', 'context': {'default_binding_id': self.id}}

    def send_sms(self, number, parameters, event_key=None, record_id=False, scheduled_at=False):
        """Enqueue atomically; the provider is contacted only by the independent worker.

        Reuse event_key for the same business event. Reuse with a different payload is rejected.
        """
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group('qing_sms.group_sms_user'):
            raise AccessError(_('需要短信发送权限。'))
        number, values, body = self._prepare_payload(number, parameters, record_id)
        event_key = event_key or uuid4().hex
        if not isinstance(event_key, str) or len(event_key) > 200:
            raise UserError(_('事件标识必须为不超过 200 字符的文本。'))
        return self.env['qing.sms.message']._enqueue(self, number, values, body, event_key, record_id,
                                                   scheduled_at=fields.Datetime.to_datetime(scheduled_at) if scheduled_at else False)

    def write(self, vals):
        if {'review_hash', 'accept_new', 'retired_at', 'scenario_id'} & vals.keys():
            raise AccessError(_('请使用核验动作确认模板。'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if any({'review_hash', 'accept_new', 'retired_at', 'scenario_id'} & values.keys() for values in vals_list):
            raise AccessError(_('请使用核验动作确认模板。'))
        return super().create(vals_list)
