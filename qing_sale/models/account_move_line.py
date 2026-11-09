from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def write(self, vals):
        if vals.get('move_id') and self.env.user._qing_sales_team_domain():
            self.env['account.move'].browse(vals['move_id']).check_access('write')
        return super().write(vals)
