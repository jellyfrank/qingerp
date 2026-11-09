from odoo import models


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    def _get_extra_price(self, currency):
        if self.env.website._qing_is_price_hidden():
            return 0.0
        return super()._get_extra_price(currency)
