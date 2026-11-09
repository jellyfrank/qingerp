from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def write(self, vals):
        if 'extra_tax_data' in vals:
            self.move_id._qing_check_fapiao_protection()
        return super().write(vals)
