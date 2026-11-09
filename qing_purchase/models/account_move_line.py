from odoo import api, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _qing_check_purchase_links(self, vals_list):
        if self.env.su or not self.env.user._qing_purchase_restricted():
            return
        purchase_lines = {vals['purchase_line_id'] for vals in vals_list if vals.get('purchase_line_id')}
        moves = {vals['move_id'] for vals in vals_list if vals.get('move_id')}
        if purchase_lines:
            self.env['purchase.order.line'].browse(purchase_lines).check_access('read')
        if moves:
            self.env['account.move'].browse(moves).check_access('write')

    @api.model_create_multi
    def create(self, vals_list):
        self._qing_check_purchase_links(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._qing_check_purchase_links([vals])
        return super().write(vals)
