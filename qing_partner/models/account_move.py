from odoo import api, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _qing_check_partner(self):
        for move in self:
            if not move.partner_id or move.move_type not in ('out_invoice', 'in_invoice', 'out_receipt', 'in_receipt'):
                continue
            role = 'customer' if move.is_sale_document() else 'supplier'
            move.partner_id._qing_check_business_role(role, move.company_id)

    def _post(self, soft=True):
        self._qing_check_partner()
        return super()._post(soft=soft)

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._qing_check_partner()
        return moves

    def write(self, values):
        result = super().write(values)
        if {'partner_id', 'company_id', 'move_type'}.intersection(values) or values.get('state') == 'posted':
            self._qing_check_partner()
        return result
