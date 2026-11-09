from odoo import api, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _qing_check_partner(self):
        for order in self:
            order.partner_id._qing_check_business_role('customer', order.company_id)

    def _action_confirm(self):
        self._qing_check_partner()
        return super()._action_confirm()

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._qing_check_partner()
        return orders

    def write(self, values):
        result = super().write(values)
        if {'partner_id', 'company_id'}.intersection(values) or values.get('state') == 'sale':
            self._qing_check_partner()
        return result
