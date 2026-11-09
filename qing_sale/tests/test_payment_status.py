from lxml import etree
import logging
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestPaymentStatus(AccountTestInvoicingCommon):
    @classmethod
    def get_default_groups(cls):
        return super().get_default_groups() | cls.env.ref('sales_team.group_sale_manager')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.partner_a
        cls.service = cls.env['product.product'].create({
            'name': 'Payment Status Service', 'type': 'service', 'taxes_id': [Command.clear()],
        })

    def _order(self, amount=1000, currency=None):
        return self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'currency_id': (currency or self.env.company.currency_id).id,
            'order_line': [Command.create({
                'product_id': self.service.id, 'product_uom_qty': 1,
                'price_unit': amount, 'tax_ids': [Command.clear()],
            })],
        })

    def _invoice(self, orders, amounts=None, currency=None):
        amounts = amounts or [order.amount_total for order in orders]
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id,
            'currency_id': (currency or orders[0].currency_id).id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'name': order.name, 'quantity': 1, 'price_unit': amount,
                'account_id': self.company_data['default_account_revenue'].id,
                'tax_ids': [Command.clear()],
                'sale_line_ids': [Command.set(order.order_line.ids)],
            }) for order, amount in zip(orders, amounts)],
        })
        invoice.action_post()
        return invoice

    def _pay(self, invoice, amount=None):
        refund = invoice.move_type == 'out_refund'
        payment = self.env['account.payment'].create({
            'payment_type': 'outbound' if refund else 'inbound',
            'partner_type': 'customer', 'partner_id': self.customer.id,
            'amount': invoice.amount_total if amount is None else amount,
            'currency_id': invoice.currency_id.id,
            'journal_id': self.company_data['default_journal_bank'].id,
            'payment_method_line_id': (
                self.outbound_payment_method_line if refund else self.inbound_payment_method_line
            ).id,
        })
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == 'asset_receivable'
        ).reconcile()
        return payment

    def test_partial_full_and_unreconcile(self):
        order = self._order()
        self.assertEqual(order.qing_payment_status, 'not_paid')
        invoice = self._invoice(order)
        self.assertEqual(order.qing_payment_status, 'not_paid')
        first = self._pay(invoice, 200)
        self.assertEqual(order.qing_payment_status, 'partial')
        self.assertEqual(order.qing_received_amount, 200)
        self._pay(invoice, 800)
        self.assertEqual(order.qing_payment_status, 'paid')
        first.move_id.line_ids.remove_move_reconcile()
        self.assertEqual(order.qing_payment_status, 'partial')
        self.assertEqual(order.qing_received_amount, 800)

    def test_paid_deposit_is_not_paid_order(self):
        order = self._order()
        deposit = self._invoice(order, [200])
        self._pay(deposit)
        self.assertEqual(order.qing_payment_status, 'partial')
        balance = self._invoice(order, [800])
        self._pay(balance)
        self.assertEqual(order.qing_payment_status, 'paid')

    def test_merged_invoice_proportional_allocation(self):
        orders = self._order(100) | self._order(300)
        invoice = self._invoice(orders)
        self._pay(invoice, 200)
        self.assertEqual(orders.mapped('qing_received_amount'), [50, 150])
        self.assertEqual(orders.mapped('qing_payment_status'), ['partial', 'partial'])
        self._pay(invoice, 200)
        self.assertEqual(orders.mapped('qing_received_amount'), [100, 300])
        self.assertEqual(orders.mapped('qing_payment_status'), ['paid', 'paid'])

    def test_ambiguous_shared_line_requires_review(self):
        orders = self._order(100) | self._order(300)
        invoice = self._invoice(orders[:1], [400])
        invoice.button_draft()
        invoice.invoice_line_ids.sale_line_ids = orders.order_line
        invoice.action_post()
        self._pay(invoice)
        self.assertEqual(orders.mapped('qing_payment_status'), ['review', 'review'])
        self.assertEqual(sum(orders.mapped('qing_received_amount')), 0)

    def test_credit_note_offset_is_not_cash_refund(self):
        order = self._order()
        invoice = self._invoice(order)
        refund = invoice._reverse_moves([{'date': fields.Date.today()}])
        refund.action_post()
        lines = (invoice.line_ids + refund.line_ids).filtered(
            lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
        )
        if lines:
            lines.reconcile()
        self.assertEqual(order.qing_payment_status, 'not_paid')
        self.assertEqual(order.qing_refunded_amount, 0)

    def test_actual_partial_and_full_refund(self):
        order = self._order()
        invoice = self._invoice(order)
        self._pay(invoice)
        refund = invoice._reverse_moves([{'date': fields.Date.today()}])
        refund.action_post()
        self._pay(refund, 200)
        self.assertEqual(order.qing_refunded_amount, 200)
        self.assertEqual(order.qing_payment_status, 'partial_refund')
        self._pay(refund, 800)
        self.assertEqual(order.qing_payment_status, 'refunded')

    def test_draft_cancelled_invoice_and_order(self):
        order = self._order()
        invoice = self._invoice(order)
        invoice.button_draft()
        invoice.button_cancel()
        self.assertEqual(order.qing_payment_status, 'not_paid')
        order.state = 'cancel'
        self.assertEqual(order.qing_payment_status, 'not_applicable')

    def test_bank_statement_receipt(self):
        order = self._order()
        invoice = self._invoice(order)
        self.pay_with_statement_line(
            invoice, self.company_data['default_journal_bank'].id, fields.Date.today(), 1000,
        )
        self.assertEqual(order.qing_payment_status, 'paid')
        self.assertEqual(order.qing_received_amount, 1000)

    def test_unallocated_partner_payment_does_not_pay_order(self):
        order = self._order()
        invoice = self._invoice(order)
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': self.customer.id, 'amount': 1000,
            'journal_id': self.company_data['default_journal_bank'].id,
            'payment_method_line_id': self.inbound_payment_method_line.id,
        })
        payment.action_post()
        self.assertEqual(order.qing_payment_status, 'not_paid')
        self.assertEqual(invoice.amount_residual, 1000)

    def test_authorized_online_transaction_is_not_receipt(self):
        order = self._order()
        provider = self.env['payment.provider'].create({
            'name': 'Status Test Provider', 'code': 'none', 'company_id': order.company_id.id,
        })
        provider.support_manual_capture = 'partial'
        method = self.env['payment.method'].create({
            'name': 'Status Test Method', 'code': 'status-test', 'provider_id': provider.id,
        })
        self.env['payment.transaction'].create({
            'provider_id': provider.id, 'payment_method_id': method.id,
            'reference': '2442-authorization', 'amount': 1000,
            'currency_id': order.currency_id.id, 'partner_id': self.customer.id,
            'sale_order_ids': [Command.set(order.ids)], 'state': 'authorized',
        })
        self.assertEqual(order.amount_paid, 1000)
        self.assertEqual(order.qing_received_amount, 0)
        self.assertEqual(order.qing_payment_status, 'not_paid')

    def test_payment_without_entry_requires_review(self):
        order = self._order()
        invoice = self._invoice(order)
        self.inbound_payment_method_line.payment_account_id = False
        with patch.object(self.env.registry['account.move'], '_get_invoice_in_payment_state', return_value='in_payment'):
            payment = self.env['account.payment'].create({
                'payment_type': 'inbound', 'partner_type': 'customer',
                'partner_id': self.customer.id, 'amount': 1000,
                'journal_id': self.company_data['default_journal_bank'].id,
                'payment_method_line_id': self.inbound_payment_method_line.id,
            })
            payment.action_post()
        self.assertFalse(payment.move_id)
        invoice.matched_payment_ids = payment
        self.assertEqual(order.qing_payment_status, 'review')

    def test_currency_precision_and_conversion(self):
        currency = self.setup_other_currency('JPY', rounding=1.0)
        order = self._order(1000, currency)
        invoice = self._invoice(order)
        self._pay(invoice, 999)
        self.assertEqual(order.qing_payment_status, 'partial')
        self._pay(invoice, 1)
        self.assertEqual(order.qing_payment_status, 'paid')
        domestic = self._order(100)
        foreign = self._invoice(domestic, [200], currency)
        expected = currency._convert(200, domestic.currency_id, domestic.company_id, foreign.date)
        self._pay(foreign)
        self.assertEqual(domestic.qing_received_amount, expected)

    def test_list_search_and_form_views(self):
        for xmlid in ('sale.view_order_tree', 'sale.sale_order_list_with_dashboard'):
            arch = self.env['sale.order'].get_view(
                view_id=self.env.ref(xmlid).id, view_type='list'
            )['arch']
            root = etree.fromstring(arch.encode())
            self.assertEqual(len(root.xpath("//field[@name='qing_payment_status']")), 1)
        paid = self._order()
        self._pay(self._invoice(paid))
        self.assertIn(paid, self.env['sale.order'].search([('qing_payment_status', '=', 'paid')]))

    def test_large_order_compute_does_not_rewrite_lines(self):
        query_counts = []
        for count in (50, 150):
            with self.subTest(lines=count):
                order = self._order()
                order.order_line = [Command.clear()] + [Command.create({
                    'product_id': self.service.id, 'product_uom_qty': 1,
                    'price_unit': 1, 'tax_ids': [Command.clear()], 'sequence': index,
                }) for index in range(count)]
                before = order.order_line.mapped('write_date')
                self._pay(self._invoice(order))
                self.env.flush_all()
                self.assertEqual(order.qing_payment_status, 'paid')
                self.assertEqual(order.order_line.mapped('write_date'), before)
                self.env.invalidate_all()
                queries = self.cr.sql_log_count
                order._compute_qing_payment_status()
                query_counts.append(self.cr.sql_log_count - queries)
                _logger.info('2442 compute: %s lines, %s queries', count, query_counts[-1])
        self.assertLessEqual(query_counts[1], query_counts[0] + 15)
