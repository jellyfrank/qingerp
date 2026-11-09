from odoo import fields, models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _qing_check_send(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('qing_whatsapp.group_user'):
            raise UserError(_('WhatsApp operator access is required.'))
        if self.state == 'cancel':
            raise UserError(_('A cancelled quotation cannot be sent.'))
        if not self.partner_id.phone:
            raise UserError(_('Set the customer phone number with country code first.'))
        return self.env['qing.whatsapp.conversation']._normalize_number(self.partner_id.phone)

    qing_conversation_id = fields.Many2one('qing.whatsapp.conversation', copy=False, check_company=True)

    def action_qing_whatsapp(self):
        number = self._qing_check_send()
        domain = [('company_id', '=', self.company_id.id), ('operator_ids', 'in', self.env.user.ids), ('active', '=', True)]
        account = self.env['qing.whatsapp.account'].search(domain, limit=1)
        if not account:
            raise UserError(_('No WhatsApp account is assigned to you for this company.'))
        wizard = self.env['qing.whatsapp.compose'].create({'sale_order_id': self.id,
            'account_id': account.id, 'number': number, 'partner_id': self.partner_id.id, 'kind': 'document'})
        wizard._prepare_sale_preview()
        return {'type': 'ir.actions.act_window', 'res_model': wizard._name, 'res_id': wizard.id,
                'view_mode': 'form', 'target': 'new', 'name': _('Send quotation by WhatsApp')}

    def action_qing_conversation(self):
        self.ensure_one()
        self.check_access('read')
        if not self.qing_conversation_id:
            raise UserError(_('Send a WhatsApp quotation first.'))
        return self.qing_conversation_id.action_open_discuss()
