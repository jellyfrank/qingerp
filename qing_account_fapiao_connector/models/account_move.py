from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def write(self, vals):
        if 'document_tax_mode' in vals:
            self._qing_check_fapiao_protection()
        return super().write(vals)
