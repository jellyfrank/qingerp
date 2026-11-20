from collections import Counter, defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.addons.qing_base.tools.channel_queue import snapshot_digest
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.orders import fetch_order, fetch_refund, nodes, shop_amount
from .store_order import CAPTURE_KINDS


class ShopifyStoreRefund(models.Model):
    _inherit = 'qing.channel.store'

    def _shopify_change_matches_refunds(self, order, previous, current):
        if not previous or any(
            previous[key] != current[key] for key in ('shipping_address_hash', 'shippingAddress', 'taxesIncluded')
        ):
            return False
        domain = [('order_id', '=', order.id), ('external_id', 'not in', previous.get('refund_ids', []))]
        refunds = self.env['qing.channel.refund'].search(domain)
        if not refunds:
            return False
        try:
            return self._shopify_refunds_explain_commercial_change(refunds, previous, current)
        except ChannelReviewRequired:
            return False

    def _shopify_refunds_explain_commercial_change(self, refunds, previous, current):
        currency = self.shopify_currency_id.name
        quantities, net_amounts, shipping_amounts = Counter(), Counter(), Counter()
        refund_total = tax_total = Decimal(0)
        for refund in refunds:
            amount = shop_amount(refund.snapshot['totalRefundedSet'], currency)
            items, _adjustments = self._shopify_refund_items(refund, amount)
            refund_total += amount
            for item in items:
                tax_total += item['tax']
                if item['shipping']:
                    shipping_amounts[item['key']] += item['net'] + (item['tax'] if current['taxesIncluded'] else 0)
                else:
                    quantities[item['key']] += item['quantity']
                    net_amounts[item['key']] += item['net']
        if (shop_amount(previous['currentTotalPriceSet'], currency) - shop_amount(current['currentTotalPriceSet'], currency)
                != refund_total
                or shop_amount(previous['currentTotalTaxSet'], currency) - shop_amount(current['currentTotalTaxSet'], currency)
                != tax_total):
            return False
        if not self._shopify_refund_line_changes_match(
            previous['lineItems'], current['lineItems'], net_amounts, 'priceAfterAllDiscountsBeforeTaxesSet', quantities,
            ('quantity', 'sku', 'variant', 'requiresShipping', 'isGiftCard', 'lineItemGroup', 'discountAllocations',
             'originalTotalSet', 'originalUnitPriceSet'),
        ):
            return False
        return self._shopify_refund_line_changes_match(
            previous['shippingLines'], current['shippingLines'], shipping_amounts, 'currentDiscountedPriceSet',
            Counter(), ('title', 'code', 'discountedPriceSet'),
        )

    def _shopify_recover_first_refund_sale_lines(self, order):
        snapshot = order.snapshot
        currency = self.shopify_currency_id
        if not snapshot.get('originalTotalPriceSet') or 'totalTaxSet' not in snapshot:
            raise ChannelReviewRequired('shopify_first_import_original_totals_required')
        if any(shop_amount(snapshot[key], currency.name)
               for key in ('originalTotalDutiesSet', 'originalTotalAdditionalFeesSet') if snapshot.get(key)):
            raise ChannelReviewRequired('shopify_duties_or_additional_fees')
        pending = self._shopify_opening_delivery_quantities(order)
        if set(pending) - set(order.line_ids.mapped('external_id')):
            raise ChannelReviewRequired('shopify_opening_delivery_line_missing')
        originals = self._shopify_original_sale_lines(order, pending)
        self._check_shopify_first_refund_amounts(order, originals)
        return originals

    def _check_shopify_first_refund_amounts(self, order, originals):
        data = order.snapshot
        currency = self.shopify_currency_id
        money = currency.name
        refund_qty, refund_net, shipping_refunds = Counter(), Counter(), Counter()
        refund_total = refund_tax = Decimal(0)
        refunds = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
        if {refund.external_id for refund in refunds} != {snapshot['id'] for snapshot in data['refunds']}:
            raise ChannelReviewRequired('shopify_first_import_refund_details_mismatch')
        for refund in refunds:
            total = shop_amount(refund.snapshot['totalRefundedSet'], money)
            items, adjustments = self._shopify_refund_items(refund, total)
            if adjustments:
                raise ChannelReviewRequired('shopify_first_import_refund_adjustment_requires_review')
            refund_total += total
            for item in items:
                refund_tax += item['tax']
                if item['shipping']:
                    shipping_refunds[item['key']] += item['net'] + (item['tax'] if data['taxesIncluded'] else 0)
                else:
                    refund_qty[item['key']] += item['quantity']
                    refund_net[item['key']] += item['net']
        original_tax = shop_amount(data['totalTaxSet'], money) if data['totalTaxSet'] else Decimal(0)
        amount_change = shop_amount(data['originalTotalPriceSet'], money) - shop_amount(data['currentTotalPriceSet'], money)
        tax_change = original_tax - shop_amount(data['currentTotalTaxSet'], money)
        if currency.compare_amounts(float(amount_change), float(refund_total)) or currency.compare_amounts(
            float(tax_change), float(refund_tax)
        ):
            raise ChannelReviewRequired('shopify_first_import_refund_details_mismatch')
        original_by_key = {val['shopify_line_key']: val for val in originals}
        lines = {line.external_id: line for line in order.line_ids if line.snapshot['quantity']}
        shipping = {line['id']: line for line in nodes(data['shippingLines'])}
        if set(refund_qty) - lines.keys() or set(shipping_refunds) - shipping.keys():
            raise ChannelReviewRequired('shopify_first_import_refund_details_mismatch')
        for key, line in lines.items():
            original = original_by_key[key]
            after_refunds = shop_amount(line.snapshot['priceAfterAllDiscountsBeforeTaxesSet'], money)
            before = Decimal(str(original['shopify_expected_base']))
            if original['product_uom_qty'] - line.quantity != refund_qty[key] or currency.compare_amounts(
                float(before - after_refunds), float(refund_net[key]),
            ):
                raise ChannelReviewRequired('shopify_first_import_refund_details_mismatch')
        for key, line in shipping.items():
            before = shop_amount(line['discountedPriceSet'], money)
            after = shop_amount(line['currentDiscountedPriceSet'], money)
            if currency.compare_amounts(float(before - after), float(shipping_refunds[key])):
                raise ChannelReviewRequired('shopify_first_import_refund_details_mismatch')

    def _shopify_refund_line_changes_match(self, previous, current, amounts, price_field, quantities, immutable_fields):
        old = {line['id']: line for line in nodes(previous)}
        new = {line['id']: line for line in nodes(current)}
        if old.keys() != new.keys() or set(amounts) - old.keys():
            return False
        currency = self.shopify_currency_id.name
        for key, line in old.items():
            revised = new[key]
            if any(line.get(field) != revised.get(field) for field in immutable_fields):
                return False
            tax_keys = ('title', 'source', 'rate', 'channelLiable')
            old_taxes = {tuple(tax.get(field) for field in tax_keys) for tax in line['taxLines']}
            revised_taxes = {tuple(tax.get(field) for field in tax_keys) for tax in revised['taxLines']}
            if old_taxes != revised_taxes:
                return False
            if line.get('currentQuantity', 0) - revised.get('currentQuantity', 0) != quantities[key]:
                return False
            if shop_amount(line[price_field], currency) - shop_amount(revised[price_field], currency) != amounts[key]:
                return False
        return True

    def _refund_financial_digest(self, data, *, legacy=False):
        connection_fields = ('refundLineItems', 'refundShippingLines', 'orderAdjustments')
        facts = {field: data.get(field) for field in ('totalRefundedSet', 'duties', *connection_fields)}
        if not legacy:
            for field in connection_fields:
                facts[field] = nodes(data.get(field) or {})
        facts['transactions'] = {tx['id']: self._shopify_transaction_facts(tx) for tx in nodes(data['transactions'])}
        return snapshot_digest(facts)

    def _refund_successes(self, refund):
        currency = self.shopify_currency_id.name
        total = shop_amount(refund.snapshot['totalRefundedSet'], currency)
        if total < 0 or not refund.processed_at:
            raise ChannelReviewRequired('shopify_refund_amount_or_date_invalid')
        transactions = refund.transaction_ids.filtered(lambda tx: tx.kind == 'REFUND')
        successful = transactions.filtered(lambda tx: tx.status == 'SUCCESS')
        amounts = [shop_amount(tx.snapshot['amountSet'], currency) for tx in successful]
        if any(amount <= 0 for amount in amounts) or sum(amounts) > total:
            raise ChannelReviewRequired('shopify_refund_exceeds_declared_total')
        return total, transactions, successful, sum(amounts)

    def _apply_shopify_refund_payment(self, action):
        tx = self.env['qing.channel.transaction'].browse(action.data['transaction']).exists()
        if not tx or tx.order_id != action.order_id or tx.kind != 'REFUND' or not tx.refund_id:
            return 'obsolete'
        if tx.status != 'SUCCESS':
            return 'obsolete'
        self._record_shopify_refund_payment(tx.refund_id, tx)

    def _record_shopify_refund_payment(self, refund, tx):
        order = refund.order_id
        refund.lock_for_update()
        if not order.sale_id:
            raise ChannelReviewRequired('shopify_refund_sale_missing')
        _total, _transactions, successful, _amount = self._refund_successes(refund)
        if tx not in successful:
            raise ChannelReviewRequired('shopify_refund_transaction_changed')
        domain = [('order_id', '=', order.id), ('external_id', '=', tx.parent_external_id),
                  ('kind', 'in', ['CAPTURE', 'SALE']), ('payment_id', '!=', False)]
        parent = self.env['qing.channel.transaction'].search(domain, limit=1)
        if not parent:
            raise DeferredChannelError('shopify_refund_original_payment_pending')
        if tx.gateway != parent.gateway:
            raise ChannelReviewRequired('shopify_refund_parent_gateway_differs')
        currency = self.shopify_currency_id.name
        parent.lock_for_update()
        domain = [('order_id', '=', order.id), ('parent_external_id', '=', parent.external_id),
                  ('kind', '=', 'REFUND'), ('payment_id', '!=', False), ('id', '!=', tx.id)]
        prior = self.env['qing.channel.transaction'].search(domain)
        prior_amount = sum(Decimal(str(item.payment_id.amount)) for item in prior)
        amount = shop_amount(tx.snapshot['amountSet'], currency)
        if prior_amount + amount > Decimal(str(parent.payment_id.amount)):
            raise ChannelReviewRequired('shopify_refund_exceeds_capture')
        return self._register_shopify_payment(tx, order.sale_id.partner_id, 'outbound')

    def _refresh_shopify_pending_refund(self, refund, remaining):
        try:
            client = self._shopify_client()
            latest = fetch_refund(client, refund.external_id)
            if self._refund_financial_digest(latest) != self._refund_financial_digest(refund.snapshot):
                snapshot = fetch_order(client, refund.order_id.external_id)
                return self._apply_shopify_order(snapshot)
        except ChannelReviewRequired:
            raise
        except DeferredChannelError as exc:
            if remaining <= 0:
                raise ChannelReviewRequired('shopify_refund_status_unavailable') from None
            delay = min(exc.retry_after or 300, remaining)
            raise DeferredChannelError(exc.code, retry_after=delay) from None
        return refund.order_id

    def _ensure_shopify_refund_verification(self, refund):
        Action = self.env['qing.channel.action']
        order = refund.order_id
        if refund.accounted or (order.historical and not order.shopify_finance_requested):
            return Action.browse()
        version = order.snapshot_version
        return Action._ensure_action(
            self, f'refund-verify:{refund.id}:{version}', 'refund_verify',
            order_id=order.id, snapshot_version=version, data={'refund': refund.id},
        )

    def _shopify_action_refund(self, action):
        return self.env['qing.channel.refund'].browse(action.data['refund']).exists().filtered(
            lambda refund: refund.order_id == action.order_id
        )

    def _shopify_refund_completion(self, total, transactions, successful, amount):
        statuses = set(transactions.mapped('status'))
        failures = {'FAILURE', 'ERROR'}
        if statuses - failures - {'SUCCESS'}:
            return 'pending'
        if successful and amount == total:
            return 'ready'
        return 'failed' if statuses & failures else 'pending'

    def _shopify_is_unfunded_cancellation_refund(self, refund, total):
        order = refund.order_id
        if total or not order.snapshot.get('cancelledAt') or nodes(refund.snapshot['transactions']):
            return False
        monetary_kinds = CAPTURE_KINDS | {'REFUND'}
        return not self.env['qing.channel.transaction'].search_count([
            ('order_id', '=', order.id), ('kind', 'in', list(monetary_kinds)),
        ], limit=1)

    def _verify_shopify_refund(self, action):
        refund = self._shopify_action_refund(action)
        if not refund:
            return 'obsolete'
        if refund.accounted:
            return
        total, transactions, successful, amount = self._refund_successes(refund)
        if self._shopify_is_unfunded_cancellation_refund(refund, total):
            refund.lock_for_update()
            refund.write({'accounted': True, 'accounted_digest': self._refund_financial_digest(refund.snapshot)})
            return
        completion = self._shopify_refund_completion(total, transactions, successful, amount)
        if completion == 'ready':
            return
        if completion == 'failed':
            raise ChannelReviewRequired('shopify_refund_failed')
        hours = self.env['ir.config_parameter'].sudo().get_int('qing_shopify.refund_pending_hours', 24)
        elapsed = int((fields.Datetime.now() - refund.processed_at).total_seconds())
        remaining = max(1, hours) * 3600 - elapsed
        if action.retry_at or remaining <= 0:
            latest_order = self._refresh_shopify_pending_refund(refund, remaining)
            if latest_order.snapshot_version != action.snapshot_version:
                return 'obsolete'
        if remaining <= 0:
            raise ChannelReviewRequired('shopify_refund_incomplete')
        raise DeferredChannelError('shopify_refund_pending', retry_after=min(300, remaining))

    def _apply_shopify_refund(self, action):
        refund = self._shopify_action_refund(action)
        if not refund:
            return 'obsolete'
        order = refund.order_id
        if refund.accounted:
            refund.lock_for_update()
            if self._refund_financial_digest(refund.snapshot) != refund.accounted_digest:
                raise ChannelReviewRequired('shopify_accounted_refund_changed')
            return
        total, transactions, successful, amount = self._refund_successes(refund)
        completion = self._shopify_refund_completion(total, transactions, successful, amount)
        if completion == 'failed':
            raise ChannelReviewRequired('shopify_refund_failed')
        if completion == 'pending':
            raise DeferredChannelError('shopify_refund_pending', retry_after=300)
        refund.lock_for_update()
        if successful.filtered(lambda tx: not tx.payment_id):
            raise DeferredChannelError('shopify_refund_payments_pending')
        sale = order.sale_id
        if not sale:
            raise ChannelReviewRequired('shopify_refund_sale_missing')
        if sale.invoice_ids.filtered(lambda invoice: invoice.state == 'draft'):
            raise ChannelReviewRequired('shopify_refund_draft_invoice_exists')

        items, adjustments = self._shopify_refund_items(refund, total)
        allocations, unbilled = self._allocate_shopify_refund(refund, items)
        if allocations or adjustments:
            refund.credit_note_id = self._create_allocated_shopify_credit(
                refund, allocations, adjustments)
        for line, qty in unbilled:
            line.shopify_unbilled_refund_qty += qty
        refund.write({'accounted': True, 'accounted_digest': self._refund_financial_digest(refund.snapshot)})
        self._reconcile_shopify_receivable(order, refund)
        self._reconcile_shopify_unbilled_refund(refund)

    def _shopify_refund_items(self, refund, total):
        data = refund.snapshot
        currency = self.shopify_currency_id.name
        if data.get('duties'):
            raise ChannelReviewRequired('shopify_refund_duties_requires_review')
        items = []
        for item in nodes(data['refundLineItems']):
            items.append({
                'key': item['lineItem']['id'], 'quantity': item['quantity'],
                'net': shop_amount(item['subtotalSet'], currency), 'tax': shop_amount(item['totalTaxSet'], currency),
                'shipping': False, 'restock_type': item['restockType'],
            })
        for shipping in nodes(data['refundShippingLines']):
            items.append({
                'key': shipping['shippingLine']['id'], 'quantity': 1,
                'net': shop_amount(shipping['subtotalAmountSet'], currency),
                'tax': shop_amount(shipping['taxAmountSet'], currency), 'shipping': True,
            })
        adjustments = []
        for item in nodes(data.get('orderAdjustments') or {}):
            amount = shop_amount(item['amountSet'], currency)
            tax = shop_amount(item['taxAmountSet'], currency)
            if tax or amount < 0:
                raise ChannelReviewRequired('shopify_taxable_or_negative_adjustment')
            if amount:
                mapping = self._shopify_mapping('adjustment', item.get('reason'))
                product = mapping.product_id
                if not product or product.type != 'service':
                    raise ChannelReviewRequired('shopify_refund_adjustment_product_required')
                adjustments.append((product, amount))
        if not items and not adjustments:
            raise ChannelReviewRequired('shopify_refund_detail_missing')
        if len({item['key'] for item in items}) != len(items):
            raise ChannelReviewRequired('shopify_refund_duplicate_detail')
        if any(item['quantity'] <= 0 or min(item['net'], item['tax']) < 0 for item in items):
            raise ChannelReviewRequired('shopify_refund_line_amount_invalid')
        adjustment_amount = sum(amount for _product, amount in adjustments)
        net_total = sum(item['net'] for item in items)
        tax_total = sum(item['tax'] for item in items)
        if net_total + tax_total + adjustment_amount != total:
            if not refund.order_id.snapshot['taxesIncluded'] or net_total + adjustment_amount != total:
                raise ChannelReviewRequired('shopify_refund_detail_total_mismatch')
            for item in items:
                item['net'] -= item['tax']
                if item['net'] < 0:
                    raise ChannelReviewRequired('shopify_refund_line_amount_invalid')
        return items, adjustments

    def _allocate_shopify_refund(self, refund, items):
        order = refund.order_id
        sale = order.sale_id
        currency = self.shopify_currency_id
        product_lines = sale.order_line.filtered(lambda line: line.shopify_line_key and not line.display_type)
        ordered = {line.shopify_line_key: line for line in product_lines}
        invoices = sale.invoice_ids.filtered(lambda move: move.state == 'posted' and move.move_type == 'out_invoice')
        invoices.lock_for_update(allow_referencing=True)
        existing_credits = (invoices.reversal_move_ids | sale.invoice_ids).filtered(
            lambda move: move.state == 'posted' and move.move_type == 'out_refund')
        if existing_credits:
            known = self.env['qing.channel.refund'].search([('credit_note_id', 'in', existing_credits.ids)])
            if existing_credits - known.credit_note_id:
                raise ChannelReviewRequired('shopify_refund_existing_manual_credit')
        posted_lines = invoices.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        ambiguous_lines = posted_lines.filtered(lambda line: len(line.sale_line_ids) > 1).sale_line_ids
        invoice_lines = posted_lines.filtered(lambda line: len(line.sale_line_ids) == 1)
        channel_lines = {line.external_id: line for line in order.line_ids}
        originals = defaultdict(list)
        for line in invoice_lines.sorted(lambda item: (item.move_id.date, item.id)):
            originals[line.sale_line_ids.id].append(line)
        credits = self.env['account.move.line'].search([
            ('shopify_refund_source_line_id', 'in', invoice_lines.ids), ('move_id.state', '=', 'posted')])
        used = credits.grouped('shopify_refund_source_line_id')
        move_taxes = {}
        allocations = []
        unbilled = []
        for item in items:
            line = ordered.get(item['key'])
            if not line or not line.product_uom_qty:
                raise ChannelReviewRequired('shopify_refund_order_line_missing')
            if line in ambiguous_lines:
                raise ChannelReviewRequired('shopify_refund_invoice_line_origin_ambiguous')
            approved = (line.extra_tax_data or {}).get('manual_tax_amounts')
            preserve_taxes = bool(approved) or any(
                (source.extra_tax_data or {}).get('manual_tax_amounts') for source in originals[line.id]
            )
            if item['shipping']:
                if originals[line.id]:
                    if len(originals[line.id]) != 1:
                        raise ChannelReviewRequired('shopify_refund_shipping_invoice_ambiguous')
                    original = originals[line.id][0]
                    past = used.get(original, credits.browse())
                    if preserve_taxes:
                        amounts = self._shopify_refund_tax_allocation(
                            original, item['net'], past, move_taxes, shipping=True)
                        if currency.compare_amounts(sum(amounts.values()), float(item['tax'])):
                            raise ChannelReviewRequired('shopify_refund_tax_allocation_requires_review')
                        item = dict(item, tax_allocation=amounts)
                    allocations.append((original, item, past))
                    continue
                if (currency.compare_amounts(float(item['net']), line.shopify_expected_base)
                        or currency.compare_amounts(float(item['tax']), line.shopify_expected_tax)):
                    raise ChannelReviewRequired('shopify_unbilled_partial_shipping_refund')
                if line.shopify_unbilled_refund_qty:
                    raise ChannelReviewRequired('shopify_refund_exceeds_original')
                unbilled.append((line, 1))
                continue
            remaining = Decimal(str(item['quantity']))
            net, tax = item['net'], item['tax']
            sources = []
            for original in originals[line.id]:
                if original.product_uom_id != line.product_uom_id:
                    raise ChannelReviewRequired('shopify_refund_invoice_uom_differs')
                past = used.get(original, credits.browse())
                capacity = Decimal(str(original.quantity)) - sum(Decimal(str(credit.quantity)) for credit in past)
                sources.append((original, past, max(0, capacity)))
            channel_line = channel_lines.get(item['key'])
            billed_to_refund = self._shopify_refund_billed_quantity(order, line, channel_line, item, sources)
            for original, past, capacity in sources:
                quantity = min(capacity, billed_to_refund)
                if not quantity:
                    continue
                fraction = quantity / remaining
                line_net = net if quantity == remaining else Decimal(str(currency.round(float(net * fraction))))
                amounts = None
                if preserve_taxes:
                    amounts = self._shopify_refund_tax_allocation(original, quantity, past, move_taxes)
                    line_tax = Decimal(str(currency.round(sum(amounts.values()))))
                else:
                    line_tax = tax if quantity == remaining else Decimal(str(currency.round(float(tax * fraction))))
                portion = dict(item, quantity=quantity, net=line_net, tax=line_tax, tax_allocation=amounts)
                allocations.append((original, portion, past))
                billed_to_refund -= quantity
                remaining -= quantity
                net -= line_net
                tax -= line_tax
            if not remaining:
                if not currency.is_zero(float(tax)):
                    raise ChannelReviewRequired('shopify_refund_tax_allocation_requires_review')
                continue
            ordered_quantity = Decimal(str(line.product_uom_qty))
            fraction = float(remaining / ordered_quantity)
            unbilled_tax = line.shopify_expected_tax * fraction
            if approved:
                # The invoiced prefix already owns its rounding. Cancel unbilled units from the tail.
                outstanding = ordered_quantity - Decimal(str(line.shopify_unbilled_refund_qty))
                through = float(outstanding / ordered_quantity)
                before = float((outstanding - remaining) / ordered_quantity)
                unbilled_tax = sum(
                    currency.round(detail['tax_amount_currency'] * through)
                    - currency.round(detail['tax_amount_currency'] * before)
                    for detail in approved.values()
                )
            if (currency.compare_amounts(float(net), line.shopify_expected_base * fraction)
                    or currency.compare_amounts(float(tax), unbilled_tax)):
                raise ChannelReviewRequired('shopify_unbilled_refund_amount_requires_review')
            unbilled.append((line, float(remaining)))
        return allocations, unbilled

    def _shopify_refund_tax_details(self, line, move_taxes):
        move = line.move_id
        if move not in move_taxes:
            move_taxes[move] = self._shopify_invoice_tax_details(move)
        details = move_taxes[move].get(line.id, {})
        currency = line.currency_id
        if (details.keys() != {str(tax.id) for tax in line.tax_ids}
                or any(currency.compare_amounts(detail['base_amount_currency'], line.price_subtotal)
                       for detail in details.values())
                or currency.compare_amounts(sum(detail['tax_amount_currency'] for detail in details.values()),
                                            line.price_total - line.price_subtotal)):
            raise ChannelReviewRequired('shopify_refund_tax_allocation_requires_review')
        return {tax: detail['tax_amount_currency'] for tax, detail in details.items()}

    def _shopify_refund_tax_allocation(self, original, portion, past, move_taxes, *, shipping=False):
        currency = original.currency_id
        unit = original.product_uom_id
        metric = 'price_subtotal' if shipping else 'quantity'
        compare = currency.compare_amounts if shipping else unit.compare
        total = original[metric]
        returned = sum(past.mapped(metric))
        cumulative = returned + float(portion)
        if (total <= 0 or float(portion) <= 0 or compare(cumulative, total) > 0
                or any(line.currency_id != currency or line.product_uom_id != unit for line in past)):
            raise ChannelReviewRequired('shopify_refund_tax_allocation_requires_review')
        source = self._shopify_refund_tax_details(original, move_taxes)
        before = {tax: currency.round(amount * returned / total) for tax, amount in source.items()}
        credited = Counter()
        for line in past:
            details = self._shopify_refund_tax_details(line, move_taxes)
            if details.keys() != source.keys():
                raise ChannelReviewRequired('shopify_refund_tax_history_requires_review')
            credited.update(details)
        if any(currency.compare_amounts(credited[tax], amount) for tax, amount in before.items()):
            raise ChannelReviewRequired('shopify_refund_tax_history_requires_review')
        return {tax: currency.round(currency.round(amount * cumulative / total) - before[tax])
                for tax, amount in source.items()}

    def _shopify_refund_billed_quantity(self, order, line, channel_line, item, sources):
        quantity = Decimal(str(item['quantity']))
        billed = sum(capacity for _original, _past, capacity in sources)
        invoiced = sum(Decimal(str(original.quantity)) for original, _past, _capacity in sources)
        ordered = Decimal(str(line.product_uom_qty))
        unbilled = ordered - invoiced - Decimal(str(line.shopify_unbilled_refund_qty))
        if unbilled < 0 or quantity > billed + unbilled:
            raise ChannelReviewRequired('shopify_refund_exceeds_original')
        if not billed or not unbilled:
            return min(billed, quantity)
        restock = item['restock_type']
        if channel_line and restock in ('CANCEL', 'RETURN') and all(
            original.shopify_billed_on_delivery for original, _past, _capacity in sources
        ):
            shipped = self._shopify_shipped_before_cutover(order, channel_line, all_dates=True)
            if invoiced <= shipped <= ordered:
                if restock == 'CANCEL':
                    cancelled = self._shopify_cancelled_refund_quantities(order)[channel_line.external_id]
                    if quantity <= unbilled and quantity <= cancelled <= ordered - shipped:
                        return 0
                    raise ChannelReviewRequired('shopify_refund_invoice_allocation_ambiguous')
                if shipped == invoiced:
                    if quantity <= billed:
                        return quantity
                    raise ChannelReviewRequired('shopify_refund_invoice_allocation_ambiguous')
        if quantity == billed + unbilled:
            return billed
        raise ChannelReviewRequired('shopify_refund_invoice_allocation_ambiguous')

    def _create_allocated_shopify_credit(self, refund, allocations, adjustments):
        sale = refund.order_id.sale_id
        invoices = self.env['account.move'].browse(sorted({line.move_id.id for line, _item, _past in allocations}))
        if not invoices and adjustments:
            invoices = sale.invoice_ids.filtered(lambda move: move.state == 'posted' and move.move_type == 'out_invoice')
        if not invoices:
            raise ChannelReviewRequired('shopify_refund_original_invoice_missing')
        original = invoices[0]
        currency = self.shopify_currency_id
        if invoices.company_id != self.company_id or invoices.currency_id != currency or any(
            invoice.partner_id != original.partner_id
            or invoice.partner_shipping_id != original.partner_shipping_id
            or invoice.journal_id != original.journal_id
            or invoice.fiscal_position_id != original.fiscal_position_id
            for invoice in invoices
        ):
            raise ChannelReviewRequired('shopify_refund_incompatible_source_invoices')
        original_accounts = self._shopify_invoice_receivable_accounts(invoices)
        if len(original_accounts) != 1:
            raise ChannelReviewRequired('shopify_refund_source_receivable_differs')
        included = refund.order_id.snapshot['taxesIncluded']
        values = [self._shopify_credit_line(original, item, past, included)
                  for original, item, past in allocations]
        for product, amount in adjustments:
            accounts = product.product_tmpl_id.with_company(self.company_id).get_product_accounts(
                fiscal_pos=original.fiscal_position_id)
            if not accounts['income']:
                raise ChannelReviewRequired('shopify_refund_adjustment_account_missing')
            values.append({'product_id': product.id, 'name': product.name, 'account_id': accounts['income'].id,
                           'quantity': 1, 'price_unit': float(amount), 'tax_ids': [Command.clear()],
                           'shopify_refund_base_expected': float(amount)})
        credit_date = refund.processed_at.date()
        self._check_shopify_exchange_rate(currency, credit_date)
        credit = self.env['account.move'].with_company(self.company_id).create({
            'move_type': 'out_refund', 'company_id': self.company_id.id, 'currency_id': currency.id,
            'partner_id': original.partner_id.id, 'invoice_date': credit_date,
            'partner_shipping_id': original.partner_shipping_id.id, 'fiscal_position_id': original.fiscal_position_id.id,
            'journal_id': original.journal_id.id, 'reversed_entry_id': original.id if len(invoices) == 1 else False,
            'invoice_origin': ', '.join(invoices.mapped('name')),
            'ref': refund.external_id, 'invoice_line_ids': [Command.create(item) for item in values],
        })
        if self._shopify_invoice_receivable_accounts(credit) != original_accounts:
            raise ChannelReviewRequired('shopify_refund_credit_receivable_differs')
        tax_allocations = {line.id: (float(item['net']), item['tax_allocation'])
                           for line, item, _past in allocations if item.get('tax_allocation') is not None}
        if tax_allocations:
            self._apply_shopify_move_tax_allocations(credit, {
                line: tax_allocations[line.shopify_refund_source_line_id.id]
                for line in credit.invoice_line_ids
                if line.shopify_refund_source_line_id.id in tax_allocations
            })
        expected = sum(val['shopify_refund_base_expected'] + val.get('shopify_refund_tax_expected', 0) for val in values)
        if currency.compare_amounts(credit.amount_total, expected):
            raise ChannelReviewRequired('shopify_credit_total_mismatch')
        for line in credit.invoice_line_ids:
            if currency.compare_amounts(line.price_subtotal, line.shopify_refund_base_expected) or currency.compare_amounts(
                line.price_total - line.price_subtotal, line.shopify_refund_tax_expected
            ):
                raise ChannelReviewRequired('shopify_credit_tax_mismatch')
        credit.action_post()
        return credit

    def _shopify_invoice_receivable_accounts(self, invoices):
        return invoices.line_ids.account_id.filtered(lambda account: account.account_type == 'asset_receivable')

    def _shopify_credit_line(self, original, item, past, included):
        currency = self.shopify_currency_id
        net, tax, quantity = float(item['net']), float(item['tax']), float(item['quantity'])
        if (currency.compare_amounts(sum(past.mapped('price_subtotal')) + net, original.price_subtotal) > 0
                or currency.compare_amounts(sum(past.mapped('price_total')) + net + tax, original.price_total) > 0):
            raise ChannelReviewRequired('shopify_refund_exceeds_original')
        price_unit = (net + tax if included else net) / quantity
        if included and item.get('tax_allocation') is not None:
            Tax = self.env['account.tax']
            # Start from the refund tax base. Rounding the tax must not change that base.
            price_unit = Tax._adapt_price_unit_to_another_taxes(
                net / quantity, original.product_id, Tax, original.tax_ids, product_uom=original.product_uom_id)
        return {'product_id': original.product_id.id, 'product_uom_id': original.product_uom_id.id,
                'name': original.name, 'account_id': original.account_id.id, 'quantity': quantity,
                'price_unit': price_unit,
                'tax_ids': [Command.set(original.tax_ids.ids)], 'shopify_refund_source_line_id': original.id,
                'shopify_refund_base_expected': net, 'shopify_refund_tax_expected': tax}

    def _reconcile_shopify_unbilled_refund(self, refund):
        posted = refund.transaction_ids.filtered(lambda tx: tx.kind == 'REFUND' and tx.payment_id)
        if not posted:
            return
        parent_ids = posted.mapped('parent_external_id')
        domain = [('order_id', '=', refund.order_id.id), ('external_id', 'in', parent_ids),
                  ('kind', 'in', ['SALE', 'CAPTURE']), ('payment_id', '!=', False)]
        original = self.env['qing.channel.transaction'].search(domain)
        self._reconcile_shopify_lines((posted | original).payment_id.move_id.line_ids)
