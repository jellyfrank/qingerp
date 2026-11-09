from odoo import models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def write(self, vals):
        self.check_access('write')
        if {'state', 'company_id'} & vals.keys():
            self.order_line.sale_line_id._qing_lock_purchase()
        result = super().write(vals)
        if {'state', 'company_id'} & vals.keys() and vals.get('state') != 'cancel':
            self.order_line._qing_check_capacity()
        return result
