from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def write(self, vals):
        if 'team_id' in vals and not self.env.su and self.filtered(lambda move: move.is_sale_document()):
            self.env.user._qing_check_sale_team(vals['team_id'])
        return super().write(vals)
