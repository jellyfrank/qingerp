from odoo import Command, models
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.orders import shop_amount


class ShopifyStoreFinance(models.Model):
    _inherit = 'qing.channel.store'

    def _apply_shopify_payment(self, action):
        tx = self.env['qing.channel.transaction'].browse(action.data['transaction']).exists()
        order = action.order_id
        if not tx or tx.order_id != order or tx.status != 'SUCCESS' or tx.kind not in ('SALE', 'CAPTURE'):
            return 'obsolete'
        sale = order.sale_id
        if sale.state not in ('sale', 'done', 'cancel'):
            raise DeferredChannelError('shopify_sale_confirmation_pending')
        self._register_shopify_payment(tx, sale.partner_id, 'inbound')
        if (self.shopify_invoice_policy == 'capture' and not order.snapshot.get('cancelledAt')
                and not sale.shopify_historical_finance and not sale.shopify_started_with_refunds):
            self._invoice_shopify_capture(order)
        self._reconcile_shopify_receivable(order)

    def _apply_shopify_recovered_invoice(self, order):
        if (self.shopify_invoice_policy != 'capture' or not order.sale_id.shopify_started_with_refunds
                or order.snapshot.get('cancelledAt')):
            return 'obsolete'
        self._invoice_shopify_capture(order)
        self._reconcile_shopify_receivable(order)

    def _invoice_shopify_capture(self, order):
        if order.sale_id.order_line.filtered(lambda line: line.product_id.invoice_policy != 'order'):
            raise ChannelReviewRequired('shopify_invoice_on_order_configuration_required')
        return self._invoice_shopify_sale(order)

    def _invoice_shopify_sale(self, order, invoice_date=None):
        sale = order.sale_id
        if sale.state != 'sale' or sale.invoice_status != 'to invoice':
            return
        invoices = sale._create_invoices()
        if invoice_date:
            invoices.invoice_date = invoice_date
        for invoice in invoices:
            self._check_shopify_exchange_rate(invoice.currency_id, invoice._get_invoice_currency_rate_date())
            self._check_invoice_tax_bases(invoice)
        if self.shopify_invoice_policy == 'shipment' and not sale.shopify_historical_finance:
            delivered_lines = invoices.invoice_line_ids.filtered(
                lambda line: line.display_type == 'product' and line.product_id.is_storable
                and line.product_id.invoice_policy == 'delivery'
            )
            delivered_lines.shopify_billed_on_delivery = True
        invoices.action_post()
        return invoices

    def _check_shopify_exchange_rate(self, currency, on_date):
        company_currency = self.company_id.currency_id
        if currency == company_currency:
            return
        rates = (currency | company_currency)._get_rates(self.company_id, on_date)
        rate_date = rates[currency.id][1]
        base_rate_date = rates[company_currency.id][1]
        if not rate_date or rate_date > on_date or (base_rate_date and base_rate_date > on_date):
            raise ChannelReviewRequired('shopify_accounting_currency_rate_missing')

    def _check_invoice_tax_bases(self, invoice):
        currency = invoice.currency_id
        adjusted = {}
        earlier_invoice_taxes = {}
        for line in invoice.invoice_line_ids.filtered(lambda item: item.display_type == 'product'):
            original = line.sale_line_ids
            if len(original) != 1 or not original.product_uom_qty:
                raise ChannelReviewRequired('shopify_invoice_line_origin_unknown')
            ratio = line.quantity / original.product_uom_qty
            if currency.compare_amounts(line.price_subtotal, original.shopify_expected_base * ratio):
                raise ChannelReviewRequired('shopify_invoice_amount_requires_review')
            approved = (original.extra_tax_data or {}).get('manual_tax_amounts')
            if approved:
                allocated = self._shopify_invoice_tax_allocation(line, original, approved, earlier_invoice_taxes)
                adjusted[line] = (original.shopify_expected_base * ratio, allocated)
            elif currency.compare_amounts(line.price_total - line.price_subtotal, original.shopify_expected_tax * ratio):
                raise ChannelReviewRequired('shopify_invoice_amount_requires_review')
        if adjusted:
            self._apply_shopify_move_tax_allocations(invoice, adjusted)

    def _shopify_invoice_tax_allocation(self, line, original, approved, earlier_invoice_taxes):
        currency = line.currency_id
        previous = original.invoice_lines.filtered(
            lambda item: item.move_id != line.move_id and item.move_id.move_type == 'out_invoice'
            and item.parent_state != 'cancel'
        )
        unit = original.product_uom_id
        if line.product_uom_id != unit or any(item.product_uom_id != unit or item.currency_id != currency
                                              for item in previous):
            raise ChannelReviewRequired('shopify_invoice_tax_allocation_requires_review')
        original_qty = original.product_uom_qty
        billed_qty = sum(previous.mapped('quantity'))
        cumulative_qty = billed_qty + line.quantity
        if line.quantity <= 0 or unit.compare(cumulative_qty, original_qty) > 0:
            raise ChannelReviewRequired('shopify_invoice_tax_allocation_requires_review')
        before = billed_qty / original_qty
        through = cumulative_qty / original_qty
        source_base = original.shopify_expected_base
        previous_base = currency.round(source_base * before)
        if currency.compare_amounts(sum(previous.mapped('price_subtotal')), previous_base):
            raise ChannelReviewRequired('shopify_invoice_tax_history_requires_review')
        if currency.compare_amounts(line.price_subtotal, currency.round(source_base * through) - previous_base):
            raise ChannelReviewRequired('shopify_invoice_amount_requires_review')
        already_billed = {tax: 0.0 for tax in approved}
        for item in previous:
            move = item.move_id
            if move not in earlier_invoice_taxes:
                earlier_invoice_taxes[move] = self._shopify_invoice_tax_details(move)
            actual = earlier_invoice_taxes[move].get(item.id, {})
            if actual.keys() != approved.keys():
                raise ChannelReviewRequired('shopify_invoice_tax_history_requires_review')
            for tax, detail in actual.items():
                if currency.compare_amounts(detail['base_amount_currency'], item.price_subtotal):
                    raise ChannelReviewRequired('shopify_invoice_tax_history_requires_review')
                already_billed[tax] += detail['tax_amount_currency']
        allocated = {}
        for tax, detail in approved.items():
            source = detail['tax_amount_currency']
            past = currency.round(source * before)
            if currency.compare_amounts(already_billed[tax], past):
                raise ChannelReviewRequired('shopify_invoice_tax_history_requires_review')
            cumulative = currency.round(source * through)
            allocated[tax] = currency.round(cumulative - past)
        return allocated

    def _shopify_invoice_tax_details(self, invoice):
        base_lines, _tax_lines = invoice._get_rounded_base_and_tax_lines()
        product_line_ids = set(invoice.invoice_line_ids.ids)
        return {
            base['record'].id: {str(detail['tax'].id): detail
                                for detail in base['tax_details']['taxes_data']}
            for base in base_lines
            if getattr(base['record'], '_name', None) == 'account.move.line' and base['record'].id in product_line_ids
        }

    def _apply_shopify_move_tax_allocations(self, move, allocations):
        currency = move.currency_id
        actual = self._shopify_invoice_tax_details(move)
        Tax = self.env['account.tax']
        updates = []
        for line, (_base, expected) in allocations.items():
            current = actual.get(line.id, {})
            if current.keys() != expected.keys():
                raise ChannelReviewRequired('shopify_tax_allocation_requires_review')
            differences = [abs(amount - current[tax]['tax_amount_currency']) for tax, amount in expected.items()
                           if currency.compare_amounts(amount, current[tax]['tax_amount_currency'])]
            if not differences:
                continue
            if not self.shopify_apply_tax_rounding:
                raise ChannelReviewRequired('shopify_tax_rounding_requires_review')
            if any(currency.compare_amounts(difference, currency.rounding) > 0 for difference in differences):
                raise ChannelReviewRequired('shopify_tax_rounding_exceeds_limit')
            base_line = move._prepare_product_base_line_for_taxes_computation(line)
            base_line['manual_tax_amounts'] = {tax: {'tax_amount_currency': amount} for tax, amount in expected.items()}
            extra = Tax._export_base_line_extra_tax_data(base_line)
            updates.append(Command.update(line.id, {'extra_tax_data': extra}))
        if updates:
            move.write({'invoice_line_ids': updates})
            actual = self._shopify_invoice_tax_details(move)
        for line, (base, expected) in allocations.items():
            current = actual.get(line.id, {})
            if (current.keys() != expected.keys()
                    or any(currency.compare_amounts(amount, current[tax]['tax_amount_currency'])
                           or currency.compare_amounts(base, current[tax]['base_amount_currency'])
                           for tax, amount in expected.items())
                    or currency.compare_amounts(line.price_subtotal, base)
                    or currency.compare_amounts(line.price_total - line.price_subtotal, sum(expected.values()))):
                raise ChannelReviewRequired('shopify_tax_allocation_requires_review')

    def _register_shopify_payment(self, transaction, partner, direction):
        transaction.lock_for_update()
        currency = self.shopify_currency_id
        data = transaction.snapshot
        if data.get('settlementCurrency') not in (False, None, currency.name):
            raise ChannelReviewRequired('shopify_payout_currency_outside_pilot')
        amount = shop_amount(data['amountSet'], currency.name)
        if amount <= 0:
            raise ChannelReviewRequired('shopify_payment_amount_invalid')
        if transaction.payment_id:
            payment = transaction.payment_id
            if (payment.currency_id != currency or currency.compare_amounts(payment.amount, float(amount))
                    or payment.payment_type != direction):
                raise ChannelReviewRequired('shopify_posted_transaction_changed')
            return payment
        journal = self._shopify_mapping('gateway', transaction.gateway).journal_id
        if not journal or journal.company_id != self.company_id:
            raise ChannelReviewRequired('shopify_gateway_mapping_required')
        if (journal.currency_id or journal.company_id.currency_id) != currency:
            raise ChannelReviewRequired('shopify_gateway_currency_mismatch')
        method_lines = journal.inbound_payment_method_line_ids if direction == 'inbound' else journal.outbound_payment_method_line_ids
        methods = method_lines.filtered(lambda method: method.code == 'manual' and method.payment_account_id)
        if len(methods) != 1 or not transaction.processed_at:
            raise ChannelReviewRequired('shopify_gateway_clearing_account_required')
        payment_date = transaction.processed_at.date()
        self._check_shopify_exchange_rate(currency, payment_date)
        payment = self.env['account.payment'].with_company(self.company_id).create({
            'payment_type': direction, 'partner_type': 'customer', 'partner_id': partner.id,
            'amount': float(amount), 'currency_id': currency.id, 'company_id': self.company_id.id,
            'journal_id': journal.id, 'payment_method_line_id': methods.id,
            'date': payment_date, 'memo': transaction.external_id,
        })
        payment.action_post()
        transaction.payment_id = payment
        return payment

    def _reconcile_shopify_receivable(self, order, refund=None):
        sale = order.sale_id
        domain = [('order_id', '=', order.id), ('payment_id', '!=', False),
                  ('kind', 'in', ['CAPTURE', 'SALE'])] if not refund else [
                      ('refund_id', '=', refund.id), ('payment_id', '!=', False)]
        payments = self.env['qing.channel.transaction'].search(domain).mapped('payment_id')
        invoices = sale.invoice_ids.filtered(lambda doc: doc.state == 'posted' and doc.move_type == 'out_invoice')
        if refund:
            invoices = refund.credit_note_id
        self._reconcile_shopify_lines(invoices.line_ids | payments.move_id.line_ids)

    def _reconcile_shopify_lines(self, lines):
        candidates = lines.filtered(lambda line: line.account_id.account_type == 'asset_receivable'
                                    and line.parent_state == 'posted' and not line.reconciled)
        for (account, _partner), account_lines in candidates.grouped(lambda line: (line.account_id, line.partner_id)).items():
            if account.reconcile and any(line.balance > 0 for line in account_lines) and any(
                line.balance < 0 for line in account_lines
            ):
                account_lines.reconcile()
