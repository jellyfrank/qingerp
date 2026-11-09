from uuid import uuid4

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SmsComposer(models.TransientModel):
    _inherit = 'sms.composer'

    @api.depends('template_id')
    def _compute_domestic_binding(self):
        Binding = self.env['qing.sms.binding']
        if not self.env.su and not self.env.user.has_group('qing_sms.group_sms_user'):
            self.domestic_binding_id = False
            return
        template_ids = self.mapped('template_id').ids
        domain = [('template_id', 'in', template_ids), ('company_id', '=', self.env.company.id)]
        bindings = Binding.search(domain) if template_ids else Binding
        bindings_by_template = bindings.grouped('template_id')
        for wizard in self:
            matches = bindings_by_template.get(wizard.template_id, Binding)
            wizard.domestic_binding_id = matches if len(matches) == 1 else False

    @api.onchange('domestic_binding_id')
    def _onchange_domestic_binding(self):
        self.domestic_value_ids = [fields.Command.clear()] + [fields.Command.create({'key': var.key, 'name': var.key})
            for var in self.domestic_binding_id.variable_ids if var.source == 'input']

    def _check_domestic_route(self):
        self.ensure_one()
        if self.domestic_binding_id:
            if self.domestic_binding_id.template_id != self.template_id:
                raise UserError(_('国内绑定必须属于当前 Odoo 模板。'))
            if self.sms_type == 'marketing':
                raise UserError(_('当前连接器仅支持国内通知和验证码。'))
            return True
        # An explicitly domestic business template must never silently fall through to IAP.
        domain = [('template_id', '=', self.template_id.id)]
        if self.template_id and self.env['qing.sms.binding'].sudo().with_context(active_test=False).search_count(domain):
            raise UserError(_('该模板需要选择本公司的国内短信绑定。'))
        return False

    def _send_domestic(self, scheduled_at=False):
        self.ensure_one()
        binding = self.domestic_binding_id
        parameters = {v.key: v.value or '' for v in self.domestic_value_ids}
        records = self._get_records()
        if records:
            rows = self._filter_out_and_handle_revoked_sms_values(self._prepare_mass_sms_values(records))
            if self.comment_single_recipient:
                records.ensure_one()
                # Preserve a number corrected in the native SMS popup.
                number = self.recipient_single_number_itf or self.recipient_single_number
                rows = {records.id: {'number': number, 'state': 'outgoing'}}
        else:
            numbers = (self.sanitized_numbers or self.numbers or self.recipient_single_number_itf or '').split(',')
            rows = {index: {'number': number.strip(), 'state': 'outgoing'} for index, number in enumerate(numbers)}
        created = self.env['qing.sms.message']
        seen = set()
        for record_id, row in rows.items():
            if row['state'] != 'outgoing':
                continue
            number, parameter_values, rendered_body = binding._prepare_payload(row['number'], parameters, record_id if records else False)
            if number in seen:
                continue
            seen.add(number)
            if self.use_exclusion_list:
                domain = [('number', '=', number)]
                if self.env['phone.blacklist'].sudo().search_count(domain):
                    continue
            message = binding.send_sms(number, parameters, event_key=f'composer:{self.domestic_event_key}:{number}',
                                       record_id=record_id if records else False, scheduled_at=scheduled_at)
            created |= message
            if records and not message.sms_id.mail_message_id:
                record = records.browse(record_id)
                if hasattr(record, 'message_post'):
                    log = record.message_post(body=_('短信已加入队列：%s', message.body),
                                              message_type='comment', subtype_xmlid='mail.mt_note')
                    message.sms_id.sudo().write({'mail_message_id': log.id, 'partner_id': row.get('partner_id')})
        if not created:
            raise UserError(_('没有可发送的收件人，请检查号码、黑名单和退订设置。'))
        self.domestic_value_ids.unlink()
        return created

    def _action_send_sms(self):
        if self._check_domestic_route():
            return self._send_domestic()
        return super()._action_send_sms()

    domestic_binding_id = fields.Many2one('qing.sms.binding', string='国内短信绑定',
        compute='_compute_domestic_binding', store=True, readonly=False)
    domestic_event_key = fields.Char(default=lambda self: uuid4().hex, readonly=True)
    domestic_value_ids = fields.One2many('qing.sms.send.value', 'composer_id', string='国内短信参数')

    def action_schedule_message(self):
        if not self._check_domestic_route():
            return super().action_schedule_message()
        if not self.scheduled_date:
            raise UserError(_('请指定计划发送时间。'))
        self._send_domestic(scheduled_at=self.scheduled_date)
        return {'type': 'ir.actions.act_window_close'}
