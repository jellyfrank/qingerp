from odoo import api, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        ids = [vals.get('move_id', self.env.context.get('default_move_id')) for vals in vals_list]
        self.env['account.move'].browse([record_id for record_id in ids if record_id])._qing_check_accrual_edit()
        return super().create(vals_list)

    def write(self, values):
        self.move_id._qing_check_accrual_edit()
        if values.get('move_id'):
            self.env['account.move'].browse(values['move_id'])._qing_check_accrual_edit()
        return super().write(values)

    def unlink(self):
        self.move_id._qing_check_accrual_edit()
        return super().unlink()
