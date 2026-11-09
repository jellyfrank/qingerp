from collections import defaultdict

from odoo import api, fields, models


PAYMENT_STATES = [
    ('not_paid', 'Unpaid'),
    ('partial', 'Partially Paid'),
    ('paid', 'Paid'),
    ('in_payment', 'Payment in Progress'),
    ('partial_refund', 'Partially Refunded'),
    ('refunded', 'Refunded'),
    ('review', 'To Review'),
    ('not_applicable', 'Not Applicable'),
]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def write(self, vals):
        if 'team_id' in vals and not self.env.su:
            self.env.user._qing_check_sale_team(vals['team_id'])
        return super().write(vals)

    def _qing_invoice_payment_shares(self, invoices):
        """Allocate receipts by linked tax-inclusive invoice line amounts.

        A line shared by multiple orders cannot be allocated reliably. Unlinked
        lines retain their share instead of inflating linked orders' receipts.
        """
        shares = defaultdict(dict)
        review = set()
        for invoice in invoices:
            amounts = defaultdict(float)
            for line in invoice.invoice_line_ids:
                if line.display_type != 'product':
                    continue
                orders = line.sale_line_ids.order_id
                if len(orders) > 1:
                    review.update(orders.ids)
                    continue
                if orders:
                    amounts[orders.id] += line.price_total
            if invoice.currency_id.is_zero(invoice.amount_total):
                review.update(amounts)
                continue
            for order_id, amount in amounts.items():
                if amount < 0 or amount > invoice.amount_total + invoice.currency_id.rounding:
                    review.add(order_id)
                    continue
                shares[order_id][invoice.id] = amount / invoice.amount_total
        return shares, review

    def _qing_invoice_receipts(self, invoices):
        """Read real payment/bank matches, excluding credit notes and write-offs."""
        receipts = defaultdict(float)
        review = set()
        lines = invoices.line_ids.filtered(
            lambda line: line.account_id.account_type == 'asset_receivable'
        )
        for line in lines:
            invoice = line.move_id
            is_refund = invoice.move_type == 'out_refund'
            partials = line.matched_debit_ids if is_refund else line.matched_credit_ids
            for partial in partials:
                counterpart = partial.debit_move_id if is_refund else partial.credit_move_id
                move = counterpart.move_id
                payment = move.origin_payment_id
                if move.state != 'posted' or move.company_id != invoice.company_id:
                    continue
                if payment:
                    direction = 'outbound' if is_refund else 'inbound'
                    if payment.state not in ('paid', 'reconciled') or payment.payment_type != direction:
                        continue
                elif not move.statement_line_id:
                    continue
                amount = partial.credit_amount_currency if is_refund else partial.debit_amount_currency
                receipts[invoice.id] += amount
        for invoice in invoices:
            # Native payments without entries lack a per-invoice amount allocation.
            if invoice.matched_payment_ids.filtered(
                lambda payment: not payment.move_id and payment.state in ('paid', 'reconciled')
            ):
                review.update(invoice.invoice_line_ids.sale_line_ids.order_id.ids)
        return receipts, review

    @api.depends(
        'amount_total', 'state', 'currency_id', 'company_id',
        'order_line.invoice_lines.sale_line_ids',
        'order_line.invoice_lines.price_total',
        'order_line.invoice_lines.move_id.invoice_line_ids.price_total',
        'order_line.invoice_lines.move_id.invoice_line_ids.sale_line_ids',
        'order_line.invoice_lines.move_id.state',
        'order_line.invoice_lines.move_id.move_type',
        'order_line.invoice_lines.move_id.amount_total',
        'order_line.invoice_lines.move_id.date',
        'order_line.invoice_lines.move_id.currency_id',
        'order_line.invoice_lines.move_id.payment_state',
        'order_line.invoice_lines.move_id.matched_payment_ids.state',
        'order_line.invoice_lines.move_id.line_ids.matched_debit_ids',
        'order_line.invoice_lines.move_id.line_ids.matched_credit_ids',
        'order_line.invoice_lines.move_id.line_ids.matched_debit_ids.credit_amount_currency',
        'order_line.invoice_lines.move_id.line_ids.matched_credit_ids.debit_amount_currency',
        'order_line.invoice_lines.move_id.line_ids.matched_debit_ids.debit_move_id.move_id.state',
        'order_line.invoice_lines.move_id.line_ids.matched_credit_ids.credit_move_id.move_id.state',
        'order_line.invoice_lines.move_id.line_ids.matched_debit_ids.debit_move_id.payment_id.state',
        'order_line.invoice_lines.move_id.line_ids.matched_credit_ids.credit_move_id.payment_id.state',
    )
    def _compute_qing_payment_status(self):
        invoices = self.invoice_ids.filtered(
            lambda invoice: invoice.state == 'posted' and invoice.move_type in ('out_invoice', 'out_refund')
        )
        shares, review = self._qing_invoice_payment_shares(invoices)
        receipts, payment_review = self._qing_invoice_receipts(invoices)
        review.update(payment_review)
        by_id = {invoice.id: invoice for invoice in invoices}
        for order in self:
            received = refunded = 0.0
            pending = False
            for invoice_id, share in shares[order.id].items():
                invoice = by_id[invoice_id]
                if invoice.company_id != order.company_id:
                    review.add(order.id)
                    continue
                amount = invoice.currency_id._convert(
                    receipts[invoice_id] * share, order.currency_id,
                    order.company_id, invoice.date,
                )
                if invoice.move_type == 'out_refund':
                    refunded += amount
                else:
                    received += amount
                    pending |= invoice.payment_state == 'in_payment'
            currency = order.currency_id
            order.qing_received_amount = currency.round(received)
            order.qing_refunded_amount = currency.round(refunded)
            if order.state == 'cancel':
                status = 'not_applicable'
            elif order.id in review:
                status = 'review'
            elif currency.compare_amounts(refunded, 0) > 0:
                status = 'refunded' if received > 0 and currency.compare_amounts(refunded, received) >= 0 else 'partial_refund'
            elif pending:
                status = 'in_payment'
            elif order.amount_total > 0 and currency.compare_amounts(received, order.amount_total) >= 0:
                status = 'paid'
            elif currency.compare_amounts(received, 0) > 0:
                status = 'partial'
            else:
                status = 'not_paid'
            order.qing_payment_status = status

    qing_payment_status = fields.Selection(
        PAYMENT_STATES, string='Payment Status', compute='_compute_qing_payment_status',
        compute_sudo=True, store=True, index=True, copy=False,
        help='Actual receipts allocated to the whole order, not its invoicing status. '
             'Merged invoices allocate receipts proportionally to linked tax-inclusive lines. '
             'Ambiguous allocations require review. Credit-note offsets are not cash refunds.',
    )
    qing_received_amount = fields.Monetary(
        string='Received Amount', compute='_compute_qing_payment_status',
        compute_sudo=True, store=True, copy=False,
    )
    qing_refunded_amount = fields.Monetary(
        string='Refunded Amount', compute='_compute_qing_payment_status',
        compute_sudo=True, store=True, copy=False,
    )
