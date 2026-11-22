from decimal import Decimal, InvalidOperation
from math import isfinite

from odoo import models


class ShopifyStoreCatalogPrice(models.Model):
    _inherit = 'qing.channel.store'

    def _catalog_price_context(self):
        pricelist = self.shopify_pricelist_id
        currency = self.shopify_currency_id
        if not pricelist or not currency or pricelist.currency_id != currency:
            return pricelist.browse()
        other_stores = self.with_context(active_test=False).search_count([
            ('id', '!=', self.id), ('shopify_pricelist_id', '=', pricelist.id),
        ], limit=1)
        return pricelist.browse() if other_stores else pricelist

    def _catalog_seed_variant_price(self, item, product, variant, pricelist):
        """Import a missing shop-specific price without changing an existing ERP rule."""
        raw = variant.get('price')
        if raw is None:
            return
        Run = self.env['qing.shopify.catalog.run']
        run = item.run_id or Run.search([
            ('store_id', '=', self.id), ('state', '=', 'done'), ('shop_currency', '!=', False),
        ], limit=1)
        if not pricelist or not run or run.shop_currency != pricelist.currency_id.name:
            return 'shopify_catalog_price_list_requires_review'
        taxes = product.with_company(self.company_id).taxes_id.filtered_domain([
            ('company_id', '=', self.company_id.id),
        ])
        if any(tax.price_include != run.shop_prices_include_tax for tax in taxes):
            return 'shopify_catalog_price_tax_basis_requires_review'
        try:
            amount = float(Decimal(str(raw)))
        except (InvalidOperation, OverflowError, ValueError):
            return 'shopify_catalog_invalid_variant_price'
        if not isfinite(amount) or amount < 0:
            return 'shopify_catalog_invalid_variant_price'
        rules = self.env['product.pricelist.item'].search([
            ('pricelist_id', '=', pricelist.id), ('product_id', '=', product.id),
        ], limit=2)
        if rules:
            if len(rules) != 1 or rules.compute_price != 'fixed' or rules.min_quantity > 1 or (
                rules.date_start or rules.date_end or pricelist.currency_id.compare_amounts(rules.fixed_price, amount)
            ):
                return 'shopify_catalog_variant_price_differs'
            return
        inherited = self.env['product.pricelist.item'].search_count([
            ('pricelist_id', '=', pricelist.id), ('product_id', '=', False),
        ], limit=1)
        if inherited:
            return 'shopify_catalog_price_rules_require_review'
        self.env['product.pricelist.item'].create({
            'pricelist_id': pricelist.id, 'product_tmpl_id': product.product_tmpl_id.id,
            'product_id': product.id, 'compute_price': 'fixed', 'fixed_price': amount,
        })
        return
