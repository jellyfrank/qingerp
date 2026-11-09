from odoo import api, models

PROTECTED_LINE_FIELDS = {'move_id', 'account_id', 'partner_id', 'currency_id', 'balance', 'debit', 'credit',
                         'amount_currency', 'price_unit', 'quantity', 'discount', 'tax_ids', 'tax_line_id',
                         'product_id', 'product_uom_id', 'date_maturity'}


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _qing_fapiao_has_nonzero_tax_rate(self):
        """Return whether this line has a nonzero percentage tax, including tax groups."""
        self.ensure_one()
        taxes = list(self.tax_ids)
        while taxes:
            tax = taxes.pop()
            if tax.amount_type == 'group':
                taxes.extend(tax.children_tax_ids)
            elif tax.amount_type in ('percent', 'division') and tax.amount != 0:
                return True
        return False

    @api.model_create_multi
    def create(self, vals_list):
        move_ids = {vals.get('move_id') or self.env.context.get('default_move_id') for vals in vals_list}
        self.env['account.move'].browse([value for value in move_ids if value])._qing_check_fapiao_protection()
        return super().create(vals_list)

    def write(self, vals):
        if (PROTECTED_LINE_FIELDS | {'name'}) & vals.keys():
            moves = self.move_id | self.env['account.move'].browse(vals.get('move_id', []))
            moves._qing_check_fapiao_protection()
        return super().write(vals)

    def unlink(self):
        self.move_id._qing_check_fapiao_protection()
        return super().unlink()
