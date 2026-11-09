from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestNativeAdvance(AccountTestInvoicingCommon):
    """Exercise real payments and bills, not synthetic reconciliation records."""

    def _payment(self, amount, date, inbound=False):
        payment = self.env['account.payment'].create({
            'partner_id': self.partner_a.id, 'partner_type': 'supplier',
            'payment_type': 'inbound' if inbound else 'outbound',
            'amount': amount, 'date': date,
            'journal_id': self.company_data['default_journal_bank'].id,
        })
        payment.action_post()
        self.assertEqual(payment.move_id.state, 'posted')
        self.assertEqual(payment.destination_account_id, self.partner_a.property_account_payable_id)
        return payment

    def _payable(self, move):
        return move.line_ids.filtered(lambda line: line.account_id.account_type == 'liability_payable')

    def _bill(self, amount=1000):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id,
            'invoice_date': '2026-09-10', 'date': '2026-09-10',
            'invoice_line_ids': [Command.create({
                'name': 'Native supplier advance test', 'quantity': 1, 'price_unit': amount,
                'account_id': self.company_data['default_account_expense'].id,
                'tax_ids': [Command.clear()],
            })],
        })
        bill.action_post()
        return bill

    def _report(self, cutoff):
        wizard = self.env['qing.partner.statement'].create({
            'date_from': '2026-09-01', 'date_to': cutoff,
            'account_type': 'liability_payable', 'partner_ids': [Command.set(self.partner_a.ids)],
        })
        return wizard._statement_data()['groups'][0]

    def test_advance_bill_tail_and_historical_statement(self):
        advance = self._payment(300, '2026-09-01')
        self.assertEqual(self._report('2026-09-05')['residual'], 300)
        self.assertEqual(self._report('2026-09-05')['aging'][5], -300)
        bill = self._bill()
        bill.js_assign_outstanding_line(self._payable(advance.move_id).id)
        self.assertEqual(bill.amount_residual, 700)
        self.assertEqual(self._report('2026-09-30')['residual'], -700)
        register = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids,
        ).create({'amount': 700, 'payment_date': '2026-10-01',
                  'journal_id': self.company_data['default_journal_bank'].id})
        register.action_create_payments()
        self.assertEqual(bill.amount_residual, 0)
        self.assertTrue(self._payable(bill).reconciled)
        self.assertEqual(self._report('2026-09-30')['residual'], -700)
        self.assertEqual(self._report('2026-10-31')['residual'], 0)
        # Paying the supplier does not itself clear the bank clearing account.
        clearing = advance.move_id.line_ids.filtered(lambda line: line.account_id == advance.outstanding_account_id)
        self.assertFalse(clearing.reconciled)

    def test_unused_advance_refund(self):
        advance = self._payment(300, '2026-09-01')
        refund = self._payment(300, '2026-09-20', inbound=True)
        (self._payable(advance.move_id) | self._payable(refund.move_id)).reconcile()
        self.assertEqual(self._report('2026-09-10')['residual'], 300)
        self.assertEqual(self._report('2026-09-30')['residual'], 0)
        self.assertTrue(self._payable(advance.move_id).reconciled)

    def test_unmatch_and_reapply_existing_payment(self):
        advance = self._payment(300, '2026-09-01')
        bill = self._bill()
        line = self._payable(advance.move_id)
        bill.js_assign_outstanding_line(line.id)
        self.assertEqual(bill.amount_residual, 700)
        count = self.env['account.payment'].search_count([])
        self._payable(bill).remove_move_reconcile()
        self.assertEqual(bill.amount_residual, 1000)
        self.assertEqual(line.amount_residual, 300)
        self.assertEqual(self._report('2026-09-30')['residual'], -700)
        bill.js_assign_outstanding_line(line.id)
        self.assertEqual(bill.amount_residual, 700)
        self.assertEqual(self.env['account.payment'].search_count([]), count)
