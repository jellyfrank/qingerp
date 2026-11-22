from odoo import models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _compute_name_short(self):
        # A blank section or note can be part of the portal's compute batch.
        unnamed_lines = self.filtered(lambda line: not line.product_id and not line.name)
        unnamed_lines.name_short = False
        super(SaleOrderLine, self - unnamed_lines)._compute_name_short()
