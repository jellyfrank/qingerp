from odoo import _, Command, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.orders import fetch_order, nodes, parse_date, shop_amount


class ShopifyOrderOpeningFinance(models.Model):
    _inherit = 'qing.channel.order'

    shopify_finance_requested = fields.Boolean(copy=False, readonly=True)

    def action_shopify_backfill_finance(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(_('Accounting Manager access is required for a Shopify financial backfill.'))
        if self.shopify_platform != 'shopify' or not self.historical:
            raise UserError(_('Choose a Shopify order from before the inventory cutover.'))
        order = self.sudo()
        order.lock_for_update()
        if order.sale_id or order.shopify_takeover_requested or order.line_ids.sale_line_id:
            raise UserError(_('This order already has ERP sales or an open delivery request. Review its accounting manually.'))
        order.shopify_finance_requested = True
        order.store_id._enqueue_order_actions(order)
        return True


class ShopifyStoreOpeningFinance(models.Model):
    _inherit = 'qing.channel.store'

    def _enqueue_shopify_opening_finance(self, order, refund_verifications):
        if order.sale_id and not order.sale_id.shopify_historical_finance:
            return
        prerequisite = None
        if not order.sale_id:
            prerequisite = self.env['qing.channel.action']._ensure_action(
                self, f'opening-finance:{order.id}:{order.snapshot_version}', 'opening_finance',
                order_id=order.id, snapshot_version=order.snapshot_version,
            )
        self._enqueue_shopify_financial_actions(order, refund_verifications, prerequisite=prerequisite)

    def _post_shopify_opening_finance(self, order):
        order.lock_for_update()
        if not order.historical or not order.shopify_finance_requested or order.sale_id:
            return 'obsolete'
        self._link_shopify_opening_commitments(order)
        if order.stock_unverified or order.line_ids.sale_line_id:
            raise ChannelReviewRequired('shopify_opening_finance_existing_or_unverified')
        snapshot = order.snapshot
        currency = self.shopify_currency_id
        if snapshot['currencyCode'] != currency.name or self.shopify_pricelist_id.currency_id != currency:
            raise ChannelReviewRequired('shopify_pricelist_currency_mismatch')
        if not snapshot.get('originalTotalPriceSet') or 'totalTaxSet' not in snapshot:
            current = fetch_order(self._shopify_client(), order.external_id)
            if not current.get('originalTotalPriceSet') or 'totalTaxSet' not in current:
                raise ChannelReviewRequired('shopify_opening_finance_original_totals_required')
            previous_version = order.snapshot_version
            self._apply_shopify_order(current)
            if order.snapshot_version == previous_version:
                raise ChannelReviewRequired('shopify_opening_finance_original_totals_required')
            return 'obsolete'
        if any(shop_amount(snapshot[key], currency.name)
               for key in ('originalTotalDutiesSet', 'originalTotalAdditionalFeesSet', 'currentTotalDutiesSet',
                           'currentTotalAdditionalFeesSet') if snapshot.get(key)):
            raise ChannelReviewRequired('shopify_duties_or_additional_fees')
        if snapshot.get('sourceName') == 'pos' or order.line_ids.filtered(lambda line: line.snapshot.get('isGiftCard')):
            raise ChannelReviewRequired('shopify_order_outside_pilot')
        pending = self._shopify_opening_delivery_quantities(order)
        if pending and not self.shopify_open_orders_verified:
            raise DeferredChannelError('shopify_opening_orders_not_confirmed')
        if set(pending) - set(order.line_ids.mapped('external_id')):
            raise ChannelReviewRequired('shopify_opening_delivery_line_missing')
        values = self._shopify_original_sale_lines(order, pending)
        partner, shipping = self._shopify_customer(order)
        sale = self.env['sale.order'].with_company(self.company_id).create({
            'partner_id': partner.id, 'partner_invoice_id': partner.id, 'partner_shipping_id': shipping.id,
            'company_id': self.company_id.id, 'warehouse_id': self.warehouse_id.id,
            'pricelist_id': self.shopify_pricelist_id.id, 'client_order_ref': order.external_name,
            'origin': order.external_name, 'shopify_snapshot_version': order.snapshot_version,
            'shopify_historical_finance': True, 'order_line': [Command.create(line) for line in values],
        })
        self._compare_shopify_sale_totals(order, sale, before_returns=True)
        by_remote_id = {line.shopify_line_key: line for line in sale.order_line}
        for line in order.line_ids:
            line.sale_line_id = by_remote_id.get(line.external_id, False)
        order.sale_id = sale
        sale.action_confirm()
        invoices = self._invoice_shopify_sale(order, invoice_date=parse_date(snapshot['createdAt']).date())
        if len(invoices or []) != 1:
            raise ChannelReviewRequired('shopify_opening_finance_invoice_missing')

    def _shopify_original_sale_lines(self, order, pending):
        currency = self.shopify_currency_id.name
        values = []
        for channel_line in order.line_ids:
            data = channel_line.snapshot
            quantity = data['quantity']
            if not quantity:
                continue
            if not channel_line.product_id or (data['requiresShipping'] and not channel_line.product_id.is_storable):
                raise ChannelReviewRequired('shopify_product_mapping_required')
            if not data.get('originalTotalSet'):
                raise ChannelReviewRequired('shopify_opening_finance_original_totals_required')
            open_quantity = pending.get(channel_line.external_id, 0)
            if data['requiresShipping'] and not order.snapshot.get('cancelledAt'):
                if data.get('unfulfilledQuantity') is None:
                    raise ChannelReviewRequired('shopify_opening_delivery_quantity_missing')
                if data['unfulfilledQuantity'] != open_quantity:
                    raise ChannelReviewRequired('shopify_opening_delivery_quantity_invalid')
            archived_fulfilled = self._shopify_shipped_before_cutover(order, channel_line)
            covered = quantity - open_quantity if data['requiresShipping'] else 0
            if not 0 <= archived_fulfilled <= covered:
                raise ChannelReviewRequired('shopify_opening_delivery_quantity_invalid')
            discounts = sum(shop_amount(value['allocatedAmountSet'], currency) for value in data['discountAllocations'])
            amount = shop_amount(data['originalTotalSet'], currency) - discounts
            if amount < 0:
                raise ChannelReviewRequired('shopify_opening_finance_original_totals_invalid')
            value = self._shopify_line_vals(
                order, channel_line.product_id, channel_line.external_id, quantity,
                {'shopMoney': {'amount': str(amount), 'currencyCode': currency}}, data['taxLines'], False,
            )
            value.update(qty_delivered=archived_fulfilled, shopify_opening_stock_covered_qty=covered,
                         shopify_opening_fulfilled_qty=archived_fulfilled)
            values.append(value)
        for shipping in nodes(order.snapshot['shippingLines']):
            mapping = self._shopify_mapping('shipping', shipping.get('code') or shipping['title'])
            if not mapping.product_id:
                raise ChannelReviewRequired('shopify_shipping_mapping_required')
            if not shipping.get('discountedPriceSet'):
                raise ChannelReviewRequired('shopify_opening_finance_original_totals_required')
            values.append(self._shopify_line_vals(order, mapping.product_id, shipping['id'], 1,
                                                  shipping['discountedPriceSet'], shipping['taxLines'], False))
        if not values:
            raise ChannelReviewRequired('shopify_order_has_no_billable_lines')
        return values
