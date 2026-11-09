from odoo import models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _is_add_to_cart_allowed(self):
        if self.env.website._qing_is_price_hidden():
            return False
        return super()._is_add_to_cart_allowed()

    def _prepare_jsonld_vals(self, **kwargs):
        values = super()._prepare_jsonld_vals(**kwargs)
        if self.env.website._qing_is_price_hidden():
            values.pop('offers', None)
        return values
