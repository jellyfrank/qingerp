from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestNativeCustomerReceipts(AccountTestInvoicingCommon):
    def _receipt(self, amount, date='2026-09-01', refund=False):
        payment = self.env['account.payment'].create({
            'partner_id': self.partner_a.id, 'partner_type': 'customer',
            'payment_type': 'outbound' if refund else 'inbound', 'amount': amount,
            'date': date, 'journal_id': self.company_data['default_journal_bank'].id,
        })
        payment.action_post()
        self.assertEqual(payment.move_id.state, 'posted')
        return payment

    def _ar(self, move):
        return move.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable')

    def _invoice(self, amount):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id,
            'invoice_date': '2026-09-10', 'date': '2026-09-10',
            'invoice_line_ids': [Command.create({
                'name': 'Native customer receipt test', 'quantity': 1, 'price_unit': amount,
                'account_id': self.company_data['default_account_revenue'].id,
                'tax_ids': [Command.clear()],
            })],
        })
        invoice.action_post()
        return invoice

    def _report(self, cutoff='2026-09-30'):
        wizard = self.env['qing.partner.statement'].create({
            'date_from': '2026-09-01', 'date_to': cutoff, 'account_type': 'asset_receivable',
            'partner_ids': [Command.set(self.partner_a.ids)],
        })
        return wizard._statement_data()['groups'][0]

    def test_one_advance_two_invoices_and_tail(self):
        receipt = self._receipt(700)
        self.assertEqual(self._report('2026-09-05')['residual'], -700)
        first, second = self._invoice(400), self._invoice(600)
        line = self._ar(receipt.move_id)
        first.js_assign_outstanding_line(line.id)
        self.assertEqual((first.amount_residual, line.amount_residual), (0, -300))
        second.js_assign_outstanding_line(line.id)
        self.assertEqual(second.amount_residual, 300)
        self.assertTrue(line.reconciled)
        self.assertEqual(self._report()['residual'], 300)
        register = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=second.ids,
        ).create({'amount': 300, 'payment_date': '2026-10-01',
                  'journal_id': self.company_data['default_journal_bank'].id})
        register.action_create_payments()
        self.assertEqual(second.amount_residual, 0)
        self.assertEqual(self._report()['residual'], 300)
        self.assertEqual(self._report('2026-10-31')['residual'], 0)

    def test_partial_credit_note_and_receipt(self):
        invoice = self._invoice(1000)
        reversal = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({'date': '2026-09-12', 'reason': 'Partial commercial credit',
                   'move_ids': [Command.set(invoice.ids)], 'journal_id': invoice.journal_id.id})
        reversal.reverse_moves()
        credit = reversal.new_move_ids
        self.assertEqual(credit.state, 'draft')
        credit.invoice_line_ids.price_unit = 200
        credit.action_post()
        self.assertEqual(credit.reversed_entry_id, invoice)
        invoice.js_assign_outstanding_line(self._ar(credit).id)
        self.assertEqual(invoice.amount_residual, 800)
        receipt = self._receipt(800, '2026-09-20')
        invoice.js_assign_outstanding_line(self._ar(receipt.move_id).id)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(self._report('2026-09-11')['residual'], 1000)
        self.assertEqual(self._report('2026-09-13')['residual'], 800)
        self.assertEqual(self._report()['residual'], 0)

    def test_unused_customer_advance_refund(self):
        receipt = self._receipt(500)
        refund = self._receipt(500, '2026-09-20', refund=True)
        (self._ar(receipt.move_id) | self._ar(refund.move_id)).reconcile()
        self.assertEqual(self._report('2026-09-10')['residual'], -500)
        self.assertEqual(self._report()['residual'], 0)

    def test_unmatch_one_invoice_preserves_other_allocation(self):
        receipt = self._receipt(700)
        first, second = self._invoice(400), self._invoice(600)
        line = self._ar(receipt.move_id)
        first.js_assign_outstanding_line(line.id)
        second.js_assign_outstanding_line(line.id)
        payment_count = self.env['account.payment'].search_count([])
        self._ar(second).remove_move_reconcile()
        self.assertEqual(first.amount_residual, 0)
        self.assertEqual(second.amount_residual, 600)
        self.assertEqual(line.amount_residual, -300)
        self.assertEqual(self._report()['residual'], 300)
        second.js_assign_outstanding_line(line.id)
        self.assertEqual(second.amount_residual, 300)
        self.assertEqual(self.env['account.payment'].search_count([]), payment_count)
