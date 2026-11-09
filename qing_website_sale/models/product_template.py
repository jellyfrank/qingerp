from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _get_additional_combination_info(
        self, product_or_template, quantity, uom, website, pricelist, fiscal_position, **kwargs
    ):
        values = super()._get_additional_combination_info(
            product_or_template, quantity, uom, website, pricelist, fiscal_position, **kwargs
        )
        if not website._qing_is_price_hidden():
            return values
        # Keep native variant metadata, but never send actual prices to public clients.
        for key in ('price', 'list_price', 'raw_pricelist_price', 'compare_list_price',
                    'base_unit_price', 'price_extra'):
            if key in values:
                values[key] = 0.0
        values.update({
            'has_discounted_price': False,
            'hide_price': True,
            'packaging_prices': {},
            'prevent_sale': True,
        })
        return values

    def _get_sales_prices(self, pricelist_sudo, fiscal_position_sudo, website):
        if website._qing_is_price_hidden():
            return {product.id: {'price_reduce': 0.0, 'raw_pricelist_price': 0.0}
                    for product in self}
        return super()._get_sales_prices(pricelist_sudo, fiscal_position_sudo, website)

    @api.model
    def _get_configurator_display_price(
        self, product_or_template, quantity, date, currency, pricelist, **kwargs
    ):
        if self.env.website._qing_is_price_hidden():
            return 0.0, False
        return super()._get_configurator_display_price(
            product_or_template, quantity, date, currency, pricelist, **kwargs
        )

    def _website_show_quick_add(self, product=None):
        if self.env.website._qing_is_price_hidden():
            return False
        return super()._website_show_quick_add(product)
