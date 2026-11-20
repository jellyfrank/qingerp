from odoo import api, fields, models
from odoo.addons.qing_channel.errors import ChannelReviewRequired

from ..services.orders import parse_date, shop_amount


class ShopifyOrder(models.Model):
    _inherit = 'qing.channel.order'

    @api.depends('snapshot', 'store_id.platform')
    def _compute_shopify_order_summary(self):
        codes = {order.snapshot.get('currencyCode') for order in self
                 if order.store_id.platform == 'shopify' and order.snapshot}
        currencies = self.env['res.currency'].with_context(active_test=False).search([
            ('name', 'in', codes - {None, False, ''}),
        ])
        currency_by_code = {currency.name: currency for currency in currencies}
        for order in self:
            order.shopify_order_date = False
            order.shopify_order_currency_id = False
            order.shopify_order_total = 0
            if order.store_id.platform != 'shopify' or not order.snapshot:
                continue
            snapshot = order.snapshot
            try:
                order.shopify_order_date = parse_date(snapshot.get('createdAt'))
            except (AttributeError, ValueError):
                pass
            currency = currency_by_code.get(snapshot.get('currencyCode'))
            if not currency:
                continue
            try:
                total = shop_amount(snapshot.get('currentTotalPriceSet') or snapshot.get('totalPriceSet'), currency.name)
            except ChannelReviewRequired:
                continue
            order.shopify_order_currency_id = currency
            order.shopify_order_total = float(total)

    shopify_order_currency_id = fields.Many2one(
        'res.currency', string='Order Currency', compute='_compute_shopify_order_summary', compute_sudo=True,
    )
    shopify_order_date = fields.Datetime(
        string='Channel Order Date', compute='_compute_shopify_order_summary', compute_sudo=True,
    )
    shopify_order_total = fields.Monetary(
        string='Order Total', currency_field='shopify_order_currency_id',
        compute='_compute_shopify_order_summary', compute_sudo=True,
    )
