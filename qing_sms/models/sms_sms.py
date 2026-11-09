import phonenumbers

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SmsSms(models.Model):
    _inherit = 'sms.sms'

    def _send(self, unlink_sent=True, raise_exception=False):
        # Restore ledger state if somebody resets a queue record; never re-submit a durable intent.
        domestic = self.filtered('domestic_message_id')
        states = {'queued': 'process', 'unknown': 'process', 'accepted': 'pending',
                  'delivered': 'sent', 'failed': 'error', 'canceled': 'canceled'}
        for sms in domestic:
            sms._update_sms_state_and_trackers(states[sms.domestic_message_id.state],
                failure_type='sms_server' if sms.domestic_message_id.state == 'failed' else False)
        native = self - domestic
        for sms in native.filtered(lambda record: not record.domestic_company_id):
            sms.domestic_company_id = sms._get_sms_company()
        domain = [('enabled', '=', True), ('active', '=', True),
                  ('company_id', 'in', native.mapped('domestic_company_id').ids)]
        companies = self.env['qing.sms.account'].sudo().search(domain).mapped('company_id')
        blocked = self.env['sms.sms']
        for sms in native:
            if sms.domestic_company_id not in companies:
                continue
            try:
                number = phonenumbers.parse(sms.number or '', 'CN')
            except phonenumbers.NumberParseException:
                continue
            if number.country_code == 86:
                blocked |= sms
        if blocked:
            if raise_exception:
                raise UserError(_('公司已启用国内短信，请选择已核验的国内模板；不能将自由正文转发到 IAP。'))
            blocked._update_sms_state_and_trackers('error', failure_type='sms_country_not_supported')
        native -= blocked
        if native:
            return super(SmsSms, native)._send(unlink_sent=unlink_sent, raise_exception=raise_exception)

    def _domestic_result_hook(self, result):
        """Provider-specific compatibility fields; no IAP billing hooks for domestic traffic."""
        return

    domestic_company_id = fields.Many2one('res.company', readonly=True, index=True)
    domestic_message_id = fields.Many2one('qing.sms.message', readonly=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if not values.get('domestic_company_id'):
                message = self.env['mail.message'].browse(values.get('mail_message_id'))
                company = message.record_company_id or self.env.company
                values['domestic_company_id'] = company.id
        return super().create(vals_list)

    def action_set_outgoing(self):
        if self.filtered('domestic_message_id'):
            raise UserError(_('国内短信不能直接重置重发。请先查询结果，确认需要再次发送后创建新的业务事件。'))
        return super().action_set_outgoing()

    def resend_failed(self):
        if self.filtered('domestic_message_id'):
            raise UserError(_('请在国内短信记录中查询结果；重新发送需要创建新的业务事件。'))
        return super().resend_failed()
