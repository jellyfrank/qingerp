from odoo import api, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _qing_check_partner(self):
        for order in self:
            order.partner_id._qing_check_business_role('supplier', order.company_id)

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._qing_check_partner()
        return orders

    def write(self, values):
        result = super().write(values)
        if {'partner_id', 'company_id'}.intersection(values) or values.get('state') in ('purchase', 'done'):
            self._qing_check_partner()
        return result

    def button_confirm(self):
        self._qing_check_partner()
        return super().button_confirm()

    def button_approve(self, force=False):
        self._qing_check_partner()
        return super().button_approve(force=force)
