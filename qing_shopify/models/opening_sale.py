from odoo import _, Command, fields, models
from odoo.exceptions import UserError
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.orders import nodes, shop_amount


class ShopifyOpeningOrder(models.Model):
    _inherit = 'qing.channel.order'

    shopify_platform = fields.Selection(related='store_id.platform')
    shopify_takeover_requested = fields.Boolean(copy=False, readonly=True)

    def action_shopify_prepare_open_delivery(self):
        self.ensure_one()
        self.check_access('read')
        self.store_id._check_channel_manager()
        if self.shopify_platform != 'shopify' or not self.historical:
            raise UserError(_('Choose a Shopify order from before the inventory cutover.'))
        order = self.sudo()
        order.lock_for_update()
        if order.sale_id or order.shopify_finance_requested:
            raise UserError(_('This channel order already has a sale or a financial backfill request.'))
        if not order.store_id.shopify_open_orders_verified:
            raise UserError(_('Review and confirm the complete opening orders before preparing an open delivery.'))
        order.shopify_takeover_requested = True
        order.store_id._enqueue_shopify_opening_actions(order)
        return True


class ShopifyStoreOpeningOrder(models.Model):
    _inherit = 'qing.channel.store'

    def _enqueue_shopify_opening_actions(self, order):
        if not order.shopify_takeover_requested:
            return
        if order.sale_id:
            if not order.sale_id.shopify_historical_takeover or not order.snapshot.get('cancelledAt'):
                return
            kind = 'opening_sale_cancel'
        else:
            kind = 'opening_sale'
        self.env['qing.channel.action']._ensure_action(
            self, f'{kind}:{order.id}:{order.snapshot_version}', kind,
            order_id=order.id, snapshot_version=order.snapshot_version,
        )

    def _shopify_opening_delivery_quantities(self, order):
        quantities = {}
        for assignment in nodes(order.snapshot['fulfillmentOrders']):
            if assignment['status'] in ('CANCELLED', 'CLOSED'):
                continue
            for item in nodes(assignment['lineItems']):
                if item['remainingQuantity'] <= 0:
                    continue
                location = assignment['assignedLocation'].get('location') or {}
                if (location.get('id') != self.shopify_location
                        or assignment['status'] not in ('OPEN', 'IN_PROGRESS')):
                    raise ChannelReviewRequired('shopify_opening_delivery_location_or_status')
                key = item['lineItem']['id']
                if key in quantities:
                    raise ChannelReviewRequired('shopify_opening_delivery_split_assignment')
                quantities[key] = item['remainingQuantity']
        return quantities

    def _prepare_shopify_opening_sale(self, order):
        order.lock_for_update()
        if not order.historical or order.sale_id or order.snapshot.get('cancelledAt') or order.shopify_finance_requested:
            return 'obsolete'
        if not self.shopify_open_orders_verified:
            raise DeferredChannelError('shopify_opening_orders_not_confirmed')
        if order.stock_unverified:
            raise ChannelReviewRequired('shopify_stock_unverified_commitment')
        if order.snapshot.get('sourceName') == 'pos':
            raise ChannelReviewRequired('shopify_order_outside_pilot')
        quantities = self._shopify_opening_delivery_quantities(order)
        lines = order.line_ids.filtered(lambda line: line.external_id in quantities)
        if len(lines) != len(quantities):
            raise ChannelReviewRequired('shopify_opening_delivery_line_missing')
        if not lines:
            order.shopify_takeover_requested = False
            return
        if lines.filtered(lambda line: not line.product_id or not line.product_id.is_storable):
            raise ChannelReviewRequired('shopify_stock_product_mapping_required')
        self._link_shopify_opening_commitments(order)
        matched = lines.filtered('sale_line_id')
        if matched.filtered(lambda line: line.sale_line_id.order_id.warehouse_id != self.warehouse_id
                            or line.sale_line_id.order_id.state == 'cancel'):
            raise ChannelReviewRequired('shopify_opening_delivery_binding_invalid')
        unmatched = lines - matched
        if not unmatched:
            sales = matched.sale_line_id.order_id
            if len(sales) == 1:
                order.sale_id = sales
            return
        currency = self.shopify_currency_id
        if order.snapshot['currencyCode'] != currency.name or self.shopify_pricelist_id.currency_id != currency:
            raise ChannelReviewRequired('shopify_pricelist_currency_mismatch')
        prepared = [self._shopify_opening_sale_line(order, line, quantities[line.external_id]) for line in unmatched]
        partner, shipping = self._shopify_customer(order)
        sale = self.env['sale.order'].with_company(self.company_id).create({
            'partner_id': partner.id, 'partner_invoice_id': partner.id, 'partner_shipping_id': shipping.id,
            'company_id': self.company_id.id, 'warehouse_id': self.warehouse_id.id,
            'pricelist_id': self.shopify_pricelist_id.id, 'client_order_ref': order.external_name,
            'origin': order.external_name, 'shopify_snapshot_version': order.snapshot_version,
            'shopify_historical_takeover': True, 'order_line': [Command.create(line) for line in prepared],
        })
        for line in sale.order_line:
            if (currency.compare_amounts(line.price_subtotal, line.shopify_expected_base)
                    or currency.compare_amounts(line.price_tax, line.shopify_expected_tax)):
                raise ChannelReviewRequired('shopify_opening_delivery_price_difference')
        by_remote_id = {line.shopify_line_key: line for line in sale.order_line}
        for line in unmatched:
            line.sale_line_id = by_remote_id[line.external_id]
        order.sale_id = sale

    def _shopify_opening_sale_line(self, order, line, quantity):
        if not 0 < quantity <= line.quantity:
            raise ChannelReviewRequired('shopify_opening_delivery_quantity_invalid')
        data = line.snapshot
        currency = self.shopify_currency_id

        def prorate(value):
            amount = shop_amount(value, currency.name) * quantity / line.quantity
            return {'shopMoney': {'currencyCode': currency.name, 'amount': str(currency.round(float(amount)))}}

        amount = prorate(data['priceAfterAllDiscountsBeforeTaxesSet'])
        taxes = [dict(tax, priceSet=prorate(tax['priceSet'])) for tax in data['taxLines']]
        return self._shopify_line_vals(order, line.product_id, line.external_id, quantity, amount, taxes, True)
