from odoo import api, models
from odoo.exceptions import UserError

from .common import is_internal


DEPOSIT_FINANCIAL_FIELDS = {'move_id', 'account_id', 'partner_id', 'debit', 'credit', 'balance',
                          'amount_currency', 'currency_id', 'company_id', 'quantity', 'price_unit', 'tax_ids'}


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _check_rental_deposit_edit(self):
        if not is_internal(self.env) and self.move_id.filtered(lambda move: move.qing_rental_deposit_id and move.state == 'posted'):
            raise UserError(self.env._('Posted rental deposit amounts and accounts cannot be edited. Record a refund instead.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not is_internal(self.env):
            moves = self.env['account.move'].browse([vals.get('move_id', self.env.context.get('default_move_id')) for vals in vals_list if vals.get('move_id', self.env.context.get('default_move_id'))])
            if moves.filtered(lambda move: move.qing_rental_deposit_id and move.state == 'posted'):
                raise UserError(self.env._('Lines cannot be added to posted rental deposit entries.'))
        return super().create(vals_list)

    def write(self, vals):
        if DEPOSIT_FINANCIAL_FIELDS.intersection(vals):
            self._check_rental_deposit_edit()
            if vals.get('move_id') and not is_internal(self.env):
                target = self.env['account.move'].browse(vals['move_id'])
                if target.qing_rental_deposit_id:
                    raise UserError(self.env._('Journal items cannot be moved to a rental deposit entry.'))
        return super().write(vals)

    def unlink(self):
        self._check_rental_deposit_edit()
        return super().unlink()
