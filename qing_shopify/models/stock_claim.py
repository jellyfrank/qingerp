from odoo import api, fields, models
from odoo.addons.qing_channel.errors import ChannelReviewRequired


class ShopifyStockClaim(models.Model):
    _inherit = 'qing.channel.stock.claim'

    @api.depends('ordered_qty', 'opening_fulfilled_qty', 'restocked_qty', 'line_id.external_id',
                 'line_id.order_id.historical', 'line_id.order_id.snapshot', 'store_id.cutover_at', 'store_id.platform')
    def _compute_shopify_opening_fulfilled_only(self):
        for claim in self:
            claim.shopify_opening_fulfilled_only = False
            if claim.ordered_qty <= 0 or claim.ordered_qty != claim.opening_fulfilled_qty or claim.restocked_qty:
                continue
            store = claim.store_id
            order = claim.line_id.order_id
            if store.platform != 'shopify' or not store.cutover_at or not order.historical or not order.snapshot:
                continue
            try:
                shipped = store._shopify_shipped_before_cutover(order, claim.line_id)
                restocks = store._shopify_line_restocks(order, claim.line_id)
            except (ChannelReviewRequired, KeyError, TypeError, ValueError, OverflowError):
                continue
            claim.shopify_opening_fulfilled_only = shipped == claim.ordered_qty and not any(
                quantity for _location, quantity in restocks.values()
            )

    shopify_opening_fulfilled_only = fields.Boolean(compute='_compute_shopify_opening_fulfilled_only', store=True)
