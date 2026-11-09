from odoo import fields, models, _
from odoo.exceptions import UserError


class WhatsappConversation(models.Model):
    _inherit = 'qing.whatsapp.conversation'

    sale_order_ids = fields.One2many('sale.order', 'qing_conversation_id')

    def action_new_quotation(self):
        self.ensure_one()
        self._check_sender()
        if not self.partner_id:
            raise UserError(_('Link a customer contact before creating a quotation.'))
        self.env['sale.order'].check_access('create')
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order', 'view_mode': 'form',
            'context': {'default_partner_id': self.partner_id.id, 'default_company_id': self.company_id.id,
                        'default_qing_conversation_id': self.id}}
