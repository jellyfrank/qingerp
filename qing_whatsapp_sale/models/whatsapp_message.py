from odoo import fields, models, _
from odoo.exceptions import AccessError


class WhatsappMessage(models.Model):
    _inherit = 'qing.whatsapp.message'

    def _preflight(self):
        super()._preflight()
        if self.sale_order_id:
            order = self.sale_order_id.with_user(self.sender_id)
            number = order._qing_check_send()
            if order.company_id != self.company_id or number != self.conversation_id.number:
                raise AccessError(_('The quotation company or recipient changed. Queue a new message.'))

    sale_order_id = fields.Many2one('sale.order', readonly=True, ondelete='restrict', index=True)
