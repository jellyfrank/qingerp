from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPaymentClearing(AccountTestInvoicingCommon):
    def _paid_bill(self):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id,
            'invoice_date': '2026-09-10', 'date': '2026-09-10',
            'invoice_line_ids': [Command.create({
                'name': 'Payment lifecycle test', 'quantity': 1, 'price_unit': 1000,
                'account_id': self.company_data['default_account_expense'].id, 'tax_ids': [Command.clear()],
            })],
        })
        bill.action_post()
        payment = self.env['account.payment'].create({
            'partner_id': self.partner_a.id, 'partner_type': 'supplier', 'payment_type': 'outbound',
            'amount': 300, 'date': '2026-09-01', 'journal_id': self.company_data['default_journal_bank'].id,
        })
        payment.action_post()
        payable = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == 'liability_payable')
        bill.js_assign_outstanding_line(payable.id)
        self.assertEqual(bill.amount_residual, 700)
        return bill, payment

    def _balance(self, cutoff='2026-09-30'):
        wizard = self.env['qing.partner.statement'].create({
            'date_from': '2026-09-01', 'date_to': cutoff, 'account_type': 'liability_payable',
            'partner_ids': [Command.set(self.partner_a.ids)],
        })
        return wizard._statement_data()['groups'][0]['residual']

    def test_cancel_payment_restores_bill(self):
        bill, payment = self._paid_bill()
        payment.action_cancel()
        self.assertEqual(payment.state, 'canceled')
        self.assertEqual(payment.move_id.state, 'cancel')
        self.assertEqual(bill.amount_residual, 1000)
        self.assertEqual(self._balance(), -1000)

    def test_reverse_payment_keeps_posted_trace(self):
        bill, payment = self._paid_bill()
        original = payment.move_id
        wizard = self.env['account.move.reversal'].create({
            'move_ids': [Command.set(original.ids)], 'journal_id': original.journal_id.id,
            'company_id': original.company_id.id, 'date': '2026-09-12', 'reason': 'Reverse erroneous payment',
        })
        wizard.reverse_moves()
        reverse = wizard.new_move_ids
        self.assertEqual(original.state, 'posted')
        self.assertEqual(reverse.state, 'posted')
        self.assertEqual(reverse.reversed_entry_id, original)
        self.assertEqual(bill.amount_residual, 1000)
        self.assertEqual(self._balance('2026-09-11'), -700)
        self.assertEqual(self._balance(), -1000)

    def test_hashed_payment_cannot_be_cancelled(self):
        bill, payment = self._paid_bill()
        payment.move_id.button_hash()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            payment.action_cancel()
        self.assertEqual(payment.move_id.state, 'posted')
        self.assertEqual(bill.amount_residual, 700)

    def test_bank_clearing_and_undo_do_not_duplicate_payable(self):
        bill, payment = self._paid_bill()
        clearing = payment.move_id.line_ids.filtered(lambda line: line.account_id == payment.outstanding_account_id)
        self.assertFalse(clearing.reconciled)
        statement = self.env['account.bank.statement.line'].create({
            'journal_id': payment.journal_id.id, 'date': '2026-09-15', 'amount': -300,
            'payment_ref': 'Synthetic bank debit', 'partner_id': self.partner_a.id,
            'counterpart_account_id': payment.outstanding_account_id.id,
        })
        bank_clearing = statement.line_ids.filtered(lambda line: line.account_id == payment.outstanding_account_id)
        (clearing | bank_clearing).reconcile()
        self.assertTrue(clearing.reconciled)
        bank = statement.line_ids.filtered(lambda line: line.account_id == payment.journal_id.default_account_id)
        self.assertEqual(sum(bank.mapped('balance')), -300)
        self.assertEqual(bill.amount_residual, 700)
        self.assertEqual(self._balance(), -700)
        statement.action_undo_reconciliation()
        self.assertFalse(clearing.reconciled)
        self.assertTrue(statement.line_ids.filtered(lambda line: line.account_id == payment.journal_id.suspense_account_id))
        self.assertEqual(bill.amount_residual, 700)
        self.assertEqual(self._balance(), -700)
