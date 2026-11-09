from odoo import models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def write(self, vals):
        if vals.get('order_id') and self.env.user._qing_sales_team_domain():
            self.env['sale.order'].browse(vals['order_id']).check_access('write')
        return super().write(vals)
