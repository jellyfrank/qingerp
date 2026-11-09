from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.addons.qing_sms.tools import plain_number


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    qing_sms_status = fields.Char(string='SMS status', compute='_compute_qing_sms_status')

    @api.depends('state')
    def _compute_qing_sms_status(self):
        for picking in self:
            picking.qing_sms_status = picking.env['qing.sms.event']._summary_for_record(
                picking, f'stock.picking:{picking.id}:delivery') if picking.id else False

    def _message_sms_with_template(self, template=False, template_xmlid=False,
                                   template_fallback='', partner_ids=False, **kwargs):
        self.ensure_one()
        kwargs.setdefault('qing_domestic_event_key', f'stock.picking:{self.id}:delivery')
        kwargs.setdefault('qing_domestic_business_event', 'stock.picking.delivered')
        if self.partner_id:
            kwargs.setdefault('qing_domestic_values', {'consignee': self.partner_id.name,
                'phone': plain_number(self.partner_id.phone or ''), 'picking': self.name})
        message = super()._message_sms_with_template(template=template, template_xmlid=template_xmlid,
            template_fallback=template_fallback, partner_ids=partner_ids, **kwargs)
        if self.state == 'done' and not self.env.context.get('skip_sms'):
            event = self.env['qing.sms.event']._capture('stock.picking.delivered', self,
                f'stock.picking:{self.id}:delivery')
            if event and not event.mail_message_id:
                event._write_internal({'mail_message_id': message.id})
        return message

    def _send_confirmation_email(self):
        """Use the same stock done hook as native stock_sms, including its suppression flags."""
        result = super()._send_confirmation_email()
        from odoo import modules
        if self.env.context.get('skip_sms') or self.env.context.get('install_demo') or modules.module.current_test:
            return result
        for picking in self.filtered(lambda item: item.picking_type_id.code == 'outgoing'):
            picking.env['qing.sms.event']._capture('stock.picking.delivered', picking,
                f'stock.picking:{picking.id}:delivery')
        return result

    def action_qing_send_sms(self):
        self.ensure_one()
        self.check_access('read')
        if self.env.context.get('skip_sms'):
            raise UserError(_('SMS is suppressed for this delivery.'))
        if self.state != 'done' or self.picking_type_id.code != 'outgoing':
            raise UserError(_('Only completed outgoing deliveries can send a delivery SMS.'))
        if not self.env.su and not self.env.user.has_group('qing_sms.group_sms_user'):
            raise UserError(_('SMS send permission is required.'))
        event_key = f'stock.picking:{self.id}:delivery'
        previous = self.env['qing.sms.message'].search([
            ('company_id', '=', self.company_id.id), ('event_key', '=', event_key)], limit=1)
        if previous:
            return self._qing_message_action(_('SMS task'), _('This delivery already has an SMS task.'))
        existing_event = self.env['qing.sms.event'].search([
            ('company_id', '=', self.company_id.id), ('event_key', '=', event_key)], limit=1)
        if existing_event:
            return self._qing_message_action(_('SMS task'), _('This delivery already has an SMS task.'))
        binding = self.company_id.qing_sms_delivery_binding_id
        if not binding:
            raise UserError(_('请在公司设置中选择发货短信模板。'))
        if not self.partner_id.phone:
            raise UserError(_('请为收件人填写手机号码。'))
        number = self.partner_id.phone
        available = {'consignee': self.partner_id.name, 'phone': plain_number(number),
                     'picking': self.name}
        if 'carrier_tracking_ref' in self._fields and self.carrier_tracking_ref:
            available['tracking'] = self.carrier_tracking_ref
        required = {variable.key for variable in binding.variable_ids if variable.source == 'input'}
        if not required <= available.keys():
            raise UserError(_('The delivery binding contains unsupported input variables.'))
        parameters = {key: available[key] for key in required}
        event = self.env['qing.sms.event']._capture_manual('stock.picking.delivered', self,
            event_key, binding, {'partner_id': self.partner_id.id, 'number': number}, parameters)
        record_id = self.id if binding.template_id.model == self._name else False
        if binding.template_id.model == 'res.partner':
            record_id = self.partner_id.id
        message = binding.send_sms(number, parameters, event_key=event_key, record_id=record_id)
        message.sudo()._write_internal({'event_id': event.id})
        event._write_internal({'state': 'enqueued'})
        if not message.sms_id.mail_message_id:
            log = self.message_post(body=_('发货短信已加入队列，请在国内短信中查看受理和送达结果。'),
                                    message_type='comment', subtype_xmlid='mail.mt_note')
            message.sms_id.sudo().write({'mail_message_id': log.id})
        return self._qing_message_action(_('短信任务'), _('已加入发送队列；重复点击不会重复发送。'))

    def action_aliyun_send_sms(self):
        """Compatibility for existing inherited views/server actions."""
        return self.action_qing_send_sms()
