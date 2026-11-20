from collections import Counter
from datetime import timedelta

from odoo import Command, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.orders import ORDER_TRANSACTIONS, fetch_order, nodes, parse_date, shop_amount


CAPTURE_KINDS = {'SALE', 'CAPTURE'}
PAYMENT_CHECK_KINDS = CAPTURE_KINDS | {'AUTHORIZATION', 'VOID'}
PAYMENT_PENDING_STATUSES = {'AWAITING_RESPONSE', 'PENDING', 'UNKNOWN'}
PAYMENT_CHECK_INTERVAL = 300
VOID_AUTHORIZATION_REVIEW = 'shopify_authorization_void_requires_review'
PRODUCT_MAPPING_ERRORS = {
    'shopify_catalog_codes_ambiguous', 'shopify_catalog_variant_unverified',
    'shopify_inventory_mapping_conflict', 'shopify_product_codes_ambiguous',
    'shopify_product_mapping_required', 'shopify_product_matches_multiple_variants',
    'shopify_sku_missing', 'shopify_stock_product_mapping_required',
}


class ShopifyStoreOrder(models.Model):
    _inherit = 'qing.channel.store'

    def _apply_shopify_order(self, snapshot):
        self.ensure_one()
        Order = self.env['qing.channel.order']
        historical = bool(self.cutover_at and parse_date(snapshot['createdAt']) < self.cutover_at)
        remote_version = parse_date(snapshot['updatedAt'])
        public_data, customer = Order._split_shopify_customer_data(snapshot)
        order = Order._upsert_snapshot(self, snapshot['id'], snapshot['name'], remote_version, public_data, historical)
        if order.external_updated_at != remote_version:
            return order
        order._retain_shopify_customer_data(customer)
        _eligible, void_requires_review = self._shopify_confirmation_transactions(order)
        self._update_shopify_void_authorization_issue(order, void_requires_review and not snapshot.get('cancelledAt'))
        stock_ready = True
        try:
            with self.env.cr.savepoint():
                self._sync_order_lines(order)
                order.stock_unverified = not historical and not order.sale_id and self._shopify_has_confirmed_shipment(snapshot)
        except ChannelReviewRequired as exc:
            order.stock_unverified = True
            stock_ready = False
            self.env['qing.channel.exception']._flag(self, order, exc.code)
        try:
            with self.env.cr.savepoint():
                self._sync_shopify_transactions(order)
        except ChannelReviewRequired as exc:
            self.env['qing.channel.exception']._flag(self, order, exc.code)
            return order
        try:
            with self.env.cr.savepoint():
                self._enqueue_order_actions(order, stock_ready=stock_ready)
        except ChannelReviewRequired as exc:
            self.env['qing.channel.exception']._flag(self, order, exc.code)
        return order

    def _sync_order_lines(self, order):
        existing = {line.external_id: line for line in order.line_ids}
        order_lines = nodes(order.snapshot['lineItems'])
        variants = self._shopify_variant_products([(data.get('variant') or {}).get('id') for data in order_lines])
        recovered_ids = []
        for data in order_lines:
            if not data['currentQuantity'] and not data['quantity'] and data['id'] not in existing:
                continue
            variant = data.get('variant') or {}
            variant_id = variant.get('id')
            try:
                product = self._resolve_shopify_product(data, variants.get(variant_id))
            except ChannelReviewRequired as exc:
                product = self.env['product.product']
                mapping_error = exc.code
            else:
                variants[variant_id] = product
            vals = {'order_id': order.id, 'external_id': data['id'], 'sku': data.get('sku'),
                    'variant_id': variant_id, 'product_id': product.id if product else False,
                    'quantity': data['currentQuantity'], 'snapshot': data}
            line = existing.get(data['id'])
            if line:
                if product and not line.product_id:
                    recovered_ids.append(line.id)
                line.write(vals)
            else:
                line = self.env['qing.channel.order.line'].create(vals)
            self._update_stock_claim(line)
            if not product and (data['currentQuantity'] or order.historical):
                self.env['qing.channel.exception']._flag(self, line, mapping_error)
        if recovered_ids:
            self.env['qing.channel.exception'].search([
                ('store_id', '=', self.id), ('object_model', '=', 'qing.channel.order.line'),
                ('object_id', 'in', recovered_ids), ('code', 'in', sorted(PRODUCT_MAPPING_ERRORS)), ('resolved', '=', False),
            ]).resolved = True
        if order.historical:
            self._link_shopify_opening_commitments(order)

    def _link_shopify_opening_commitments(self, order):
        by_id = {line.external_id: line for line in order.line_ids}
        commitments = self.env['qing.shopify.opening.commitment'].search([
            ('store_id', '=', self.id), ('external_line_id', 'in', list(by_id)),
        ])
        for commitment in commitments:
            try:
                commitment._attach_imported_sale_line(by_id[commitment.external_line_id])
            except ValidationError:
                raise ChannelReviewRequired('shopify_opening_commitment_match_requires_review') from None

    def _update_stock_claim(self, line):
        data = line.snapshot
        order = line.order_id
        claims = self.env['qing.channel.stock.claim']
        if not data['requiresShipping']:
            return
        domain = [('line_id', '=', line.id)]
        claim = claims.search(domain, limit=1)
        fulfilled = self._shopify_shipped_before_cutover(order, line)
        open_quantity, destinations = self._shopify_line_open_quantity(order, line)
        cancelled = data['quantity'] - data['currentQuantity']
        if open_quantity is not None or order.snapshot.get('cancelledAt'):
            shipped = self._shopify_shipped_before_cutover(order, line, all_dates=True)
            remaining = 0 if order.snapshot.get('cancelledAt') else open_quantity
            cancelled = data['quantity'] - shipped - remaining
            if cancelled < 0:
                raise ChannelReviewRequired('shopify_stock_line_quantities_inconsistent')
        restocks = self._shopify_line_restocks(order, line).values()
        restocked = sum(quantity for location, quantity in restocks if location == self.shopify_location)
        missing_return_location = any(quantity and not location for location, quantity in restocks)
        vals = {'line_id': line.id, 'ordered_qty': data['quantity'], 'restocked_qty': restocked,
                'cancelled_qty': max(0, cancelled), 'opening_fulfilled_qty': fulfilled,
                'location_uncertain': missing_return_location or bool(destinations - {self.shopify_location})}
        if claim:
            claim.write(vals)
        else:
            claims.create(vals)

    def _shopify_line_open_quantity(self, order, line):
        """Count outstanding fulfilment separately from refunds of shipped units."""
        destinations = set()
        open_quantity = None
        for assignment in nodes(order.snapshot['fulfillmentOrders']):
            if assignment['status'] == 'CANCELLED':
                continue
            connection = assignment.get('lineItems')
            if connection is not None:
                matched = [item for item in nodes(connection) if item['lineItem']['id'] == line.external_id]
                if not matched:
                    continue
                open_quantity = (open_quantity or 0) + (
                    sum(item['remainingQuantity'] for item in matched) if assignment['status'] != 'CLOSED' else 0
                )
            location = assignment['assignedLocation'].get('location') or {}
            destinations.add(location.get('id'))
        return open_quantity, destinations

    def _shopify_cancelled_refund_quantities(self, order):
        cancelled = Counter()
        for refund in order.snapshot.get('refunds', []):
            for detail in nodes(refund['refundLineItems']):
                if detail['restockType'] == 'CANCEL':
                    cancelled[detail['lineItem']['id']] += detail['quantity']
        return cancelled

    def _shopify_fulfillable_after_cancellation(self, line, cancelled):
        order = line.order_id
        if order.snapshot.get('cancelledAt'):
            return 0
        if not cancelled:
            return None
        remote_shipped = self._shopify_shipped_before_cutover(order, line, all_dates=True)
        sale_line = line.sale_line_id
        product_uom = sale_line.product_id.uom_id
        delivered = sale_line.move_ids.filtered(lambda move: move.state == 'done' and move.location_dest_usage == 'customer')
        erp_shipped = sale_line.shopify_opening_fulfilled_qty + sum(
            move.uom_id._compute_quantity(move.quantity, product_uom, round=False) for move in delivered
        )
        if product_uom.compare(erp_shipped, remote_shipped):
            return 0
        remaining = max(0, line.snapshot['quantity'] - remote_shipped - cancelled)
        open_qty, _destinations = self._shopify_line_open_quantity(order, line)
        return min(remaining, open_qty) if open_qty is not None else remaining

    def _check_shopify_cancelled_outbound(self, order):
        cancelled = self._shopify_cancelled_refund_quantities(order)
        for line in order.line_ids.filtered(lambda line: line.sale_line_id and cancelled[line.external_id]):
            moves = line.sale_line_id.move_ids.filtered(
                lambda move: move.state not in ('draft', 'done', 'cancel') and move.location_dest_usage == 'customer')
            product_uom = line.sale_line_id.product_id.uom_id
            demanded = sum(move.uom_id._compute_quantity(move.product_uom_qty, product_uom, round=False) for move in moves)
            if demanded and product_uom.compare(
                demanded, self._shopify_fulfillable_after_cancellation(line, cancelled[line.external_id]),
            ) > 0:
                raise ChannelReviewRequired('shopify_cancelled_outbound_quantity_requires_review')

    def _shopify_line_restocks(self, order, line):
        restocked = {}
        for refund in order.snapshot.get('refunds', []):
            processed_at = parse_date(refund.get('processedAt'))
            for item in nodes(refund['refundLineItems']):
                if item['lineItem']['id'] != line.external_id or not item.get('restocked'):
                    continue
                if (order.historical and processed_at and processed_at < self.cutover_at
                        and item['restockType'] == 'RETURN'):
                    continue
                location = (item.get('location') or {}).get('id')
                restocked[item['id']] = (location, item['quantity'])
        return restocked

    def _shopify_shipped_before_cutover(self, order, line, all_dates=False):
        if not all_dates and not order.historical:
            return 0
        return sum(
            detail['quantity']
            for fulfillment in order.snapshot.get('fulfillments', [])
            if fulfillment['status'] == 'SUCCESS'
            and (all_dates or parse_date(fulfillment['createdAt']) < self.cutover_at)
            for detail in nodes(fulfillment['fulfillmentLineItems'])
            if detail['lineItem']['id'] == line.external_id
        )

    def _sync_shopify_transactions(self, order):
        transactions = {item['id']: (item, False) for item in order.snapshot.get('transactions', [])}
        Refund = self.env['qing.channel.refund']
        for data in order.snapshot.get('refunds', []):
            domain = [('store_id', '=', self.id), ('external_id', '=', data['id'])]
            refund = Refund.search(domain, limit=1)
            vals = {'store_id': self.id, 'order_id': order.id, 'external_id': data['id'],
                    'snapshot': data, 'processed_at': parse_date(data['processedAt'])}
            if refund:
                if refund.accounted and self._refund_financial_digest(data) != refund.accounted_digest:
                    self.env['qing.channel.exception']._flag(self, refund, 'shopify_accounted_refund_changed')
                refund.write(vals)
            else:
                refund = Refund.create(vals)
            for data_transaction in nodes(data['transactions']):
                transactions[data_transaction['id']] = (data_transaction, refund.id)
        Transaction = self.env['qing.channel.transaction']
        domain = [('order_id', '=', order.id)]
        previous = {tx.external_id: tx for tx in Transaction.search(domain)}
        for remote_id, (data, refund_id) in transactions.items():
            vals = {'store_id': self.id, 'order_id': order.id, 'external_id': remote_id, 'snapshot': data,
                    'kind': data['kind'], 'status': data['status'], 'gateway': data['gateway'],
                    'refund_id': refund_id, 'processed_at': parse_date(data['processedAt']),
                    'parent_external_id': (data.get('parentTransaction') or {}).get('id')}
            tx = previous.get(remote_id)
            if tx:
                if tx.payment_id and self._shopify_transaction_facts(tx.snapshot) != self._shopify_transaction_facts(data):
                    self.env['qing.channel.exception']._flag(self, tx, 'shopify_posted_transaction_changed')
                tx.write(vals)
            else:
                Transaction.create(vals)

    def _shopify_transaction_facts(self, data):
        amount = (data.get('amountSet') or {}).get('shopMoney') or {}
        return (amount.get('currencyCode'), amount.get('amount'), data.get('status'), data.get('kind'),
                data.get('gateway'), (data.get('parentTransaction') or {}).get('id'), data.get('settlementCurrency'))

    def _enqueue_order_actions(self, order, stock_ready=True):
        self._enqueue_shopify_payment_verification(order)
        if order.historical and not order.shopify_finance_requested:
            if stock_ready:
                self._enqueue_shopify_opening_actions(order)
            return
        refunds = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
        refund_verifications = {refund: self._ensure_shopify_refund_verification(refund) for refund in refunds}
        if not stock_ready:
            return
        if order.historical:
            self._enqueue_shopify_opening_finance(order, refund_verifications)
            return
        Action = self.env['qing.channel.action']
        common = {'order_id': order.id, 'snapshot_version': order.snapshot_version}
        sale = Action._ensure_action(self, f'sale:{order.id}:{order.snapshot_version}', 'sale', **common)
        if order.snapshot.get('cancelledAt') and not order.sale_id:
            return
        if sale.state == 'review' and not order.sale_id:
            self._retry_shopify_sale_after_mapping(sale)
        self._enqueue_shopify_financial_actions(
            order, refund_verifications, prerequisite=sale if not order.sale_id else None,
        )

    def _pending_shopify_payment_ids(self, order):
        return {tx['id'] for tx in (order.snapshot or {}).get('transactions', [])
                if tx['kind'] in PAYMENT_CHECK_KINDS and tx['status'] in PAYMENT_PENDING_STATUSES}

    def _enqueue_shopify_payment_verification(self, order):
        if order.historical and not order.shopify_finance_requested:
            return
        if self._pending_shopify_payment_ids(order):
            version = order.snapshot_version
            self.env['qing.channel.action']._ensure_action(
                self, f'payment-verify:{order.id}:{version}', 'payment_verify',
                order_id=order.id, snapshot_version=version,
                retry_at=fields.Datetime.now() + timedelta(seconds=PAYMENT_CHECK_INTERVAL),
            )

    def _verify_shopify_pending_payments(self, action):
        order = action.order_id
        pending = self._pending_shopify_payment_ids(order)
        if not pending:
            return 'obsolete'
        client = self._shopify_client()
        latest = client.query(ORDER_TRANSACTIONS, {'id': order.external_id}).get('order')
        if not latest:
            raise ChannelReviewRequired('shopify_payment_order_unavailable')
        transactions = latest['transactions']
        if len(transactions) >= 100:
            raise ChannelReviewRequired('shopify_order_detail_limit_reached')
        observed = {tx['id']: self._shopify_transaction_facts(tx)
                    for tx in transactions if tx['kind'] in PAYMENT_CHECK_KINDS}
        if not pending.issubset(observed):
            raise ChannelReviewRequired('shopify_pending_payment_not_visible')
        stored = {tx['id']: self._shopify_transaction_facts(tx)
                  for tx in order.snapshot['transactions'] if tx['kind'] in PAYMENT_CHECK_KINDS}
        if observed != stored or parse_date(latest['updatedAt']) != order.external_updated_at:
            refreshed = self._apply_shopify_order(fetch_order(client, order.external_id))
            if refreshed.snapshot_version != action.snapshot_version:
                return 'obsolete'
        raise DeferredChannelError('shopify_payment_pending', retry_after=PAYMENT_CHECK_INTERVAL)

    def _retry_shopify_sale_after_mapping(self, action):
        if action.order_id.line_ids.filtered(lambda line: line.quantity and not line.product_id):
            return
        codes = ['shopify_product_mapping_required']
        currency = self.shopify_currency_id
        if (self.shopify_pricelist_id.currency_id == currency
                and action.order_id.snapshot.get('currencyCode') == currency.name):
            codes.append('shopify_pricelist_currency_mismatch')
        errors = self.env['qing.channel.exception'].search([
            ('store_id', '=', self.id), ('object_model', '=', action._name),
            ('object_id', '=', action.id), ('code', 'in', codes), ('resolved', '=', False),
        ])
        if errors:
            action.write({'state': 'pending', 'retry_at': False, 'tries': 0})
            errors.resolved = True

    def _enqueue_shopify_financial_actions(self, order, refund_verifications, prerequisite=None):
        Action = self.env['qing.channel.action']
        common = {'order_id': order.id, 'snapshot_version': order.snapshot_version}
        depends = [Command.set(prerequisite.ids)] if prerequisite else []
        Transaction = self.env['qing.channel.transaction']
        domain = [('order_id', '=', order.id), ('status', '=', 'SUCCESS'), ('kind', 'in', list(CAPTURE_KINDS))]
        payment_actions = Action.browse()
        refund_actions = Action.browse()
        for tx in Transaction.search(domain):
            payment_actions |= Action._ensure_action(self, f'payment:{tx.id}:{order.snapshot_version}', 'payment',
                                                     dependency_ids=depends, data={'transaction': tx.id}, **common)
        for refund, verification in refund_verifications.items():
            for tx in refund.transaction_ids.filtered(lambda item: item.kind == 'REFUND' and item.status == 'SUCCESS'):
                Action._ensure_action(self, f'refund-payment:{tx.id}:{order.snapshot_version}', 'refund_payment',
                                      dependency_ids=depends, data={'transaction': tx.id}, **common)
            dependencies = [Command.set(((prerequisite or Action) | verification).ids)]
            refund_actions |= Action._ensure_action(self, f'refund:{refund.id}:{order.snapshot_version}', 'refund',
                                                     dependency_ids=dependencies, data={'refund': refund.id}, **common)
            if order.historical and refund.processed_at and refund.processed_at < self.cutover_at:
                continue
            if any(line['restockType'] == 'RETURN' for line in nodes(refund.snapshot['refundLineItems'])):
                Action._ensure_action(self, f'return:{refund.id}:{order.snapshot_version}', 'return',
                                      dependency_ids=depends, data={'refund': refund.id}, **common)
        if (not order.historical and self.shopify_invoice_policy == 'capture' and payment_actions and refund_actions
                and (not order.sale_id or order.sale_id.shopify_started_with_refunds)):
            Action._ensure_action(
                self, f'first-invoice:{order.id}:{order.snapshot_version}', 'recovered_invoice',
                dependency_ids=[Command.set((payment_actions | refund_actions).ids)], **common,
            )

    def _execute_channel_action(self, action):
        if self.platform != 'shopify':
            return super()._execute_channel_action(action)
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        if action.order_id and action.snapshot_version != action.order_id.snapshot_version:
            return 'obsolete'
        if action.kind == 'sale':
            return self._apply_shopify_sale(action.order_id)
        if action.kind == 'opening_sale':
            return self._prepare_shopify_opening_sale(action.order_id)
        if action.kind == 'opening_finance':
            return self._post_shopify_opening_finance(action.order_id)
        if action.kind == 'opening_sale_cancel':
            return self._apply_shopify_cancel(action.order_id)
        if action.kind == 'payment':
            return self._apply_shopify_payment(action)
        if action.kind == 'payment_verify':
            return self._verify_shopify_pending_payments(action)
        if action.kind == 'recovered_invoice':
            return self._apply_shopify_recovered_invoice(action.order_id)
        if action.kind == 'refund_verify':
            return self._verify_shopify_refund(action)
        if action.kind == 'refund_payment':
            return self._apply_shopify_refund_payment(action)
        if action.kind == 'refund':
            return self._apply_shopify_refund(action)
        return super()._execute_channel_action(action)

    def _apply_shopify_sale(self, order):
        order.lock_for_update()
        sale = order.sale_id
        data = order.snapshot
        if data.get('cancelledAt'):
            return self._apply_shopify_cancel(order)
        if not sale and self._shopify_has_confirmed_shipment(data):
            raise ChannelReviewRequired('shopify_first_import_shipment_requires_review')
        if sale and sale.state == 'cancel':
            raise ChannelReviewRequired('shopify_sale_already_cancelled')
        version = sale.shopify_snapshot_version if sale else 0
        if version == order.snapshot_version:
            return
        if data.get('sourceName') == 'pos' or any(line.snapshot.get('isGiftCard') for line in order.line_ids):
            raise ChannelReviewRequired('shopify_order_outside_pilot')
        if sale and data.get('refunds'):
            self._check_shopify_cancelled_outbound(order)
        commercial = self._commercial_data(data)
        previous = self._shopify_commercial_facts(sale.shopify_commercial_data)
        unchanged = all(previous.get(key) == value for key, value in commercial.items() if key != 'refund_ids')
        if sale and not unchanged and data.get('refunds') and self._shopify_change_matches_refunds(order, previous, commercial):
            return
        if sale and (sale.invoice_ids.filtered(lambda invoice: invoice.state != 'cancel')
                     or sale.picking_ids.filtered(lambda picking: picking.state == 'done')):
            if unchanged:
                return
            raise ChannelReviewRequired('shopify_edit_after_invoice_or_delivery')
        currency = self.shopify_currency_id
        if data.get('currencyCode') != currency.name or self.shopify_pricelist_id.currency_id != currency:
            raise ChannelReviewRequired('shopify_pricelist_currency_mismatch')
        if any(shop_amount(data[key], currency.name) for key in ('currentTotalDutiesSet', 'currentTotalAdditionalFeesSet')
               if data.get(key)):
            raise ChannelReviewRequired('shopify_duties_or_additional_fees')
        if data.get('refunds') and sale and not unchanged:
            raise ChannelReviewRequired('shopify_order_edit_with_refunds_requires_review')
        recovered_refunds = not sale and bool(data.get('refunds'))
        line_vals = self._shopify_recover_first_refund_sale_lines(order) if recovered_refunds else self._shopify_sale_lines(order)
        self._validate_single_location(order)
        partner, ship = self._shopify_customer(order)
        vals = {'partner_id': partner.id, 'partner_invoice_id': partner.id, 'partner_shipping_id': ship.id,
                'company_id': self.company_id.id, 'warehouse_id': self.warehouse_id.id,
                'pricelist_id': self.shopify_pricelist_id.id, 'client_order_ref': data['name'],
                'origin': data['name'], 'shopify_snapshot_version': order.snapshot_version,
                'shopify_commercial_data': commercial}
        if sale and sale.state == 'sale':
            if unchanged:
                return
            self._update_confirmed_shopify_sale(order, vals, line_vals)
        else:
            vals['order_line'] = [Command.clear(), *(Command.create(line) for line in line_vals)]
            if sale:
                sale.write(vals)
            else:
                sale = self.env['sale.order'].with_company(self.company_id).create(
                    dict(vals, shopify_started_with_refunds=recovered_refunds))
                order.sale_id = sale
        self._compare_shopify_sale_totals(order, sale, before_returns=recovered_refunds)
        by_remote_id = {line.shopify_line_key: line for line in sale.order_line}
        for line in order.line_ids:
            line.sale_line_id = by_remote_id.get(line.external_id, False)
        transactions, _void_requires_review = self._shopify_confirmation_transactions(order)
        should_confirm, unmapped = self._shopify_sale_confirmation(transactions)
        if unmapped:
            self.env['qing.channel.exception']._flag(self, order, 'shopify_gateway_mapping_required')
        if sale.state in ('draft', 'sent') and should_confirm and not unmapped:
            sale.action_confirm()

    def _shopify_has_confirmed_shipment(self, data):
        return any(
            fulfillment['status'] == 'SUCCESS' and nodes(fulfillment['fulfillmentLineItems'])
            for fulfillment in data.get('fulfillments', [])
        )

    def _apply_shopify_cancel(self, order):
        sale = order.sale_id
        data = order.snapshot
        if not sale:
            if self._shopify_has_confirmed_shipment(data):
                raise ChannelReviewRequired('shopify_first_import_shipment_requires_review')
            if any(tx['kind'] in CAPTURE_KINDS and tx['status'] == 'SUCCESS'
                   for tx in data.get('transactions', [])):
                raise ChannelReviewRequired('shopify_first_import_funded_cancellation_requires_review')
            if data.get('refunds'):
                external_ids = {row['id'] for row in data['refunds']}
                refunds = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
                currency = self.shopify_currency_id.name
                if set(refunds.mapped('external_id')) != external_ids or any(
                    not self._shopify_is_unfunded_cancellation_refund(
                        refund, shop_amount(refund.snapshot['totalRefundedSet'], currency),
                    ) for refund in refunds
                ):
                    raise ChannelReviewRequired('shopify_first_import_funded_cancellation_requires_review')
            return
        if sale.state == 'cancel':
            return
        if sale.picking_ids.filtered(lambda picking: picking.state == 'done') or self._shopify_has_confirmed_shipment(data):
            raise ChannelReviewRequired('shopify_cancel_after_delivery')
        posted = sale.invoice_ids.filtered(lambda invoice: invoice.state == 'posted' and invoice.move_type == 'out_invoice')
        if posted:
            refunds = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
            if not refunds:
                raise ChannelReviewRequired('shopify_cancel_invoice_requires_refund')
            if refunds.filtered(lambda refund: not refund.accounted):
                raise DeferredChannelError('shopify_cancel_waiting_for_credit')
            credits = refunds.credit_note_id.filtered(lambda credit: credit.state == 'posted')
            by_original = credits.invoice_line_ids.filtered(
                'shopify_refund_source_line_id'
            ).grouped(lambda line: line.shopify_refund_source_line_id.move_id)
            for invoice in posted:
                amounts = by_original.get(invoice, self.env['account.move.line']).mapped('price_total')
                if invoice.currency_id.compare_amounts(invoice.amount_total, sum(amounts)):
                    raise ChannelReviewRequired('shopify_cancel_invoice_not_fully_credited')
        sale._action_cancel()

    def _validate_single_location(self, order):
        assignments = nodes(order.snapshot['fulfillmentOrders'])
        locations = {(entry['assignedLocation']['location'] or {}).get('id') for entry in assignments
                     if entry['status'] not in ('CANCELLED', 'CLOSED')}
        if locations - {self.shopify_location}:
            raise ChannelReviewRequired('shopify_location_outside_pilot')
        if not assignments and any(line.snapshot['requiresShipping'] and line.quantity for line in order.line_ids):
            raise DeferredChannelError('shopify_location_assignment_pending')

    def _commercial_data(self, snapshot):
        keys = ('lineItems', 'shippingLines', 'taxesIncluded', 'currentTotalPriceSet', 'currentTotalTaxSet',
                'shippingAddress', 'shipping_address_hash')
        data = {key: snapshot[key] for key in keys}
        data['refund_ids'] = sorted(refund['id'] for refund in snapshot.get('refunds', []))
        return self._shopify_commercial_facts(data)

    def _shopify_commercial_facts(self, data):
        if not data:
            return {}
        lines = []
        for item in nodes(data['lineItems']):
            line = dict(item)
            line.pop('unfulfilledQuantity', None)
            if line.get('variant'):
                variant = dict(line['variant'])
                variant.pop('barcode', None)
                line['variant'] = variant
            lines.append(line)
        return dict(data, lineItems={'nodes': lines}, shippingLines={'nodes': nodes(data['shippingLines'])})

    def _shopify_confirmation_transactions(self, order):
        transactions = [transaction for transaction in (order.snapshot or {}).get('transactions', [])
                        if transaction['status'] == 'SUCCESS']
        voided_ids = {(transaction.get('parentTransaction') or {}).get('id')
                      for transaction in transactions if transaction['kind'] == 'VOID'}
        parents_known = all(voided_ids)
        eligible = [transaction for transaction in transactions if transaction['kind'] in CAPTURE_KINDS or (
            transaction['kind'] == 'AUTHORIZATION' and parents_known and transaction['id'] not in voided_ids
        )]
        return eligible, bool(voided_ids and not eligible)

    def _update_shopify_void_authorization_issue(self, order, needs_review):
        ExceptionModel = self.env['qing.channel.exception']
        if needs_review:
            ExceptionModel._flag(self, order, VOID_AUTHORIZATION_REVIEW)
            return
        ExceptionModel.search([
            ('store_id', '=', self.id), ('object_model', '=', order._name), ('object_id', '=', order.id),
            ('code', '=', VOID_AUTHORIZATION_REVIEW), ('resolved', '=', False),
        ]).resolved = True

    def _shopify_sale_confirmation(self, transactions):
        gateways = {transaction['gateway'] for transaction in transactions}
        mapped = all(self._shopify_mapping('gateway', gateway).journal_id for gateway in gateways)
        if len(gateways) > 1:
            raise ChannelReviewRequired('shopify_multiple_gateways')
        return bool(transactions), not mapped

    def _shopify_mapping(self, kind, key):
        domain = [('store_id', '=', self.id), ('kind', '=', kind), ('external_key', '=', key or '-')]
        return self.env['qing.channel.mapping'].search(domain, limit=1)

    def _shopify_customer(self, order):
        data = order._read_shopify_customer_data()
        address = data.get('shippingAddress') or data.get('billingAddress')
        if not address or not address.get('address1'):
            raise ChannelReviewRequired('shopify_shipping_address_unavailable')
        Partner = self.env['res.partner']
        Binding = self.env['qing.channel.binding']
        key = (data.get('customer') or {}).get('id') or 'guest:' + order.external_id
        domain = [('store_id', '=', self.id), ('kind', '=', 'customer'), ('external_id', '=', key)]
        binding = Binding.search(domain, limit=1)
        bill_address = data.get('billingAddress') or address
        if binding:
            partner = binding.partner_id
        else:
            values = self._shopify_partner_vals(bill_address, data.get('email'), data.get('phone'))
            partner = Partner.create(dict(values, company_id=self.company_id.id))
            Binding.create({'store_id': self.id, 'kind': 'customer', 'external_id': key, 'partner_id': partner.id})
        order_key = 'shipping:' + order.external_id
        ship_domain = [('store_id', '=', self.id), ('kind', '=', 'customer'), ('external_id', '=', order_key)]
        ship_binding = Binding.search(ship_domain, limit=1)
        ship_vals = self._shopify_partner_vals(address, None, None)
        if ship_binding:
            ship = ship_binding.partner_id
            ship.write(ship_vals)
        else:
            ship = Partner.create(dict(ship_vals, parent_id=partner.id, company_id=self.company_id.id, type='delivery'))
            Binding.create({'store_id': self.id, 'kind': 'customer', 'external_id': order_key, 'partner_id': ship.id})
        return partner, ship

    def _shopify_partner_vals(self, address, email, phone):
        country_domain = [('code', '=', address.get('countryCodeV2'))]
        country = self.env['res.country'].search(country_domain, limit=1)
        state_domain = [('country_id', '=', country.id), ('code', '=', address.get('provinceCode'))]
        state = self.env['res.country.state'].search(state_domain, limit=1)
        name = ' '.join(value for value in (address.get('firstName'), address.get('lastName')) if value)
        if not name or not country:
            raise ChannelReviewRequired('shopify_customer_fields_unavailable')
        return {'name': name, 'email': email, 'phone': address.get('phone') or phone,
                'street': address.get('address1'), 'street2': address.get('address2'),
                'city': address.get('city'), 'zip': address.get('zip'),
                'country_id': country.id, 'state_id': state.id}

    def _shopify_sale_lines(self, order):
        vals = []
        for line in order.line_ids.filtered('quantity'):
            if not line.product_id:
                raise ChannelReviewRequired('shopify_product_mapping_required')
            data = line.snapshot
            vals.append(self._shopify_line_vals(order, line.product_id, line.external_id, line.quantity,
                                               data['priceAfterAllDiscountsBeforeTaxesSet'], data['taxLines'], True))
        for shipping in nodes(order.snapshot['shippingLines']):
            mapping = self._shopify_mapping('shipping', shipping.get('code') or shipping['title'])
            if not mapping.product_id:
                raise ChannelReviewRequired('shopify_shipping_mapping_required')
            vals.append(self._shopify_line_vals(order, mapping.product_id, shipping['id'], 1,
                                               shipping['currentDiscountedPriceSet'], shipping['taxLines'], False))
        if not vals:
            raise ChannelReviewRequired('shopify_order_has_no_billable_lines')
        return vals

    def _shopify_line_vals(self, order, product, key, qty, amount, taxes, is_net):
        data = order.snapshot
        currency = self.shopify_currency_id.name
        address = data.get('shippingAddress') or data.get('billingAddress') or {}
        tax_model = self.env['account.tax']
        tax_total = sum(shop_amount(tax['priceSet'], currency) for tax in taxes)
        mapped_amounts = {}
        for tax in taxes:
            if tax['channelLiable'] is None:
                raise ChannelReviewRequired('shopify_tax_responsibility_unknown')
            key_parts = [address.get('countryCodeV2'), address.get('provinceCode'), tax.get('source'),
                         tax['title'], 'included' if data['taxesIncluded'] else 'excluded',
                         'channel' if tax['channelLiable'] else 'merchant']
            tax_key = '|'.join(value or '-' for value in key_parts)
            local_tax = self._shopify_mapping('tax', tax_key).tax_id
            if not local_tax or local_tax.amount_type != 'percent' or abs(local_tax.amount / 100 - float(tax['rate'])) > 1e-7:
                raise ChannelReviewRequired('shopify_tax_mapping_required')
            if local_tax.price_include != data['taxesIncluded']:
                raise ChannelReviewRequired('shopify_tax_inclusion_mismatch')
            if local_tax.id in mapped_amounts:
                raise ChannelReviewRequired('shopify_tax_mapping_is_ambiguous')
            tax_model |= local_tax
            mapped_amounts[local_tax.id] = shop_amount(tax['priceSet'], currency)
        raw = shop_amount(amount, currency)
        net = raw if is_net or not data['taxesIncluded'] else raw - tax_total
        gross = net + tax_total
        price = float((gross if data['taxesIncluded'] else net) / qty)
        rounding = self._shopify_tax_rounding(product, price, qty, net, tax_model, mapped_amounts)
        return {'product_id': product.id, 'product_uom_qty': qty, 'product_uom_id': product.uom_id.id,
                'price_unit': price, 'extra_tax_data': rounding,
                'discount': 0, 'tax_ids': [Command.set(tax_model.ids)], 'shopify_line_key': key,
                'shopify_expected_base': float(net), 'shopify_expected_tax': float(tax_total)}

    def _shopify_tax_rounding(self, product, price, quantity, net, taxes, expected):
        if not taxes:
            return False
        Tax = self.env['account.tax']
        company = self.company_id
        currency = self.shopify_currency_id
        line = Tax._prepare_base_line_for_taxes_computation(
            {}, product_id=product, product_uom_id=product.uom_id, quantity=quantity, discount=0,
            price_unit=price, tax_ids=taxes, currency_id=currency, company_id=company)
        Tax._add_tax_details_in_base_line(line, company)
        Tax._round_base_lines_tax_details([line], company)
        details = line['tax_details']
        if currency.compare_amounts(details['total_excluded_currency'], float(net)):
            raise ChannelReviewRequired('shopify_line_tax_base_mismatch')
        manual = {}
        for detail in details['taxes_data']:
            tax = detail['tax']
            supplied = float(expected[tax.id])
            computed = detail['tax_amount_currency']
            if currency.compare_amounts(detail['base_amount_currency'], float(net)):
                raise ChannelReviewRequired('shopify_compounded_tax_requires_review')
            if currency.compare_amounts(computed, supplied):
                if not self.shopify_apply_tax_rounding:
                    raise ChannelReviewRequired('shopify_tax_rounding_requires_review')
                if currency.compare_amounts(abs(computed - supplied), currency.rounding) > 0:
                    raise ChannelReviewRequired('shopify_tax_rounding_exceeds_limit')
                manual[str(tax.id)] = {'tax_amount_currency': supplied}
            else:
                manual[str(tax.id)] = {'tax_amount_currency': computed}
        if any(currency.compare_amounts(detail['tax_amount_currency'], float(expected[detail['tax'].id]))
               for detail in details['taxes_data']):
            line['manual_tax_amounts'] = manual
            return Tax._export_base_line_extra_tax_data(line)
        return False

    def _compare_shopify_sale_totals(self, order, sale, before_returns=False):
        currency = self.shopify_currency_id
        for line in sale.order_line:
            if currency.compare_amounts(line.price_subtotal, line.shopify_expected_base) != 0:
                raise ChannelReviewRequired('shopify_line_tax_base_mismatch')
            if currency.compare_amounts(line.price_tax, line.shopify_expected_tax) != 0:
                raise ChannelReviewRequired('shopify_tax_rounding_requires_review')
        data = order.snapshot
        total_key = 'originalTotalPriceSet' if before_returns else 'currentTotalPriceSet'
        tax_key = 'totalTaxSet' if before_returns else 'currentTotalTaxSet'
        if currency.compare_amounts(sale.amount_total, float(shop_amount(data[total_key], currency.name))):
            raise ChannelReviewRequired('shopify_order_total_mismatch')
        tax = 0 if before_returns and data[tax_key] is None else shop_amount(data[tax_key], currency.name)
        if currency.compare_amounts(sale.amount_tax, float(tax)):
            raise ChannelReviewRequired('shopify_order_tax_mismatch')

    def _update_confirmed_shopify_sale(self, order, vals, line_vals):
        sale = order.sale_id
        current = {line.shopify_line_key: line for line in sale.order_line}
        commands = []
        for line in line_vals:
            existing = current.pop(line['shopify_line_key'], None)
            if existing and existing.product_id.id != line['product_id']:
                raise ChannelReviewRequired('shopify_confirmed_product_changed')
            commands.append(Command.update(existing.id, line) if existing else Command.create(line))
        for existing in current.values():
            commands.append(Command.update(existing.id, {'product_uom_qty': 0}))
        sale.write(dict(vals, order_line=commands))
