from odoo import models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def write(self, vals):
        if not self.env.su and vals.get('order_id') and self.env.user._qing_purchase_restricted():
            self.env['purchase.order'].browse(vals['order_id']).check_access('write')
        return super().write(vals)
