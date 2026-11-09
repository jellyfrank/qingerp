from unittest.mock import patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestBankWorkbench(AccountTestInvoicingCommon):
    def _line(self, amount=-300, **values):
        return self.env['account.bank.statement.line'].create(dict({
            'journal_id': self.company_data['default_journal_bank'].id,
            'date': '2026-09-10', 'amount': amount, 'payment_ref': 'Workbench synthetic',
        }, **values))

    def _payment(self, amount=300, partner=None, date='2026-09-10', posted=True, **values):
        payment = self.env['account.payment'].create(dict({
            'partner_id': (partner or self.partner_a).id, 'partner_type': 'supplier',
            'payment_type': 'outbound', 'amount': amount, 'date': date,
            'journal_id': self.company_data['default_journal_bank'].id,
        }, **values))
        if posted:
            payment.action_post()
        return payment

    def test_candidates_filter_rank_and_confirm(self):
        line = self._line(partner_id=self.partner_a.id)
        other = self._payment(partner=self.partner_b)
        matching = self._payment(date='2026-09-09')
        self._payment(amount=400)
        self._payment(posted=False)
        self._payment(payment_type='inbound')
        result = line.qing_workbench_candidates()
        self.assertEqual([x['id'] for x in result['candidates']], [matching.id, other.id])
        self.assertIn('往来单位一致', result['candidates'][0]['reasons'])
        item = result['candidates'][0]
        line.qing_workbench_match(item['id'], item['token'])
        self.assertTrue(line.is_reconciled)
        self.assertFalse(any(x['id'] == line.id for x in line.qing_workbench_queue()['lines']))
        line.action_qing_undo_match()
        self.assertFalse(line.is_reconciled)

    def test_stale_payment_is_rejected(self):
        line = self._line()
        payment = self._payment()
        item = line.qing_workbench_candidates()['candidates'][0]
        payment.date = '2026-09-11'
        with self.assertRaises(UserError):
            line.qing_workbench_match(payment.id, item['token'])
        self.assertFalse(line.is_reconciled)

    def test_stale_line_is_rejected(self):
        line = self._line()
        payment = self._payment()
        item = line.qing_workbench_candidates()['candidates'][0]
        line.payment_ref = 'Updated label'
        with self.assertRaises(UserError):
            line.qing_workbench_match(payment.id, item['token'])

    def test_cleared_payment_disappears(self):
        first, second = self._line(), self._line()
        payment = self._payment()
        old = second.qing_workbench_candidates()['candidates'][0]
        item = first.qing_workbench_candidates()['candidates'][0]
        first.qing_workbench_match(payment.id, item['token'])
        self.assertFalse(second.qing_workbench_candidates()['candidates'])
        with self.assertRaises(UserError):
            second.qing_workbench_match(payment.id, old['token'])

    def test_search_and_dates(self):
        line = self._line()
        payment = self._payment()
        self.assertEqual(line.qing_workbench_candidates(query=payment.name)['candidates'][0]['id'], payment.id)
        self.assertFalse(line.qing_workbench_candidates(query='NO_MATCH_XYZ')['candidates'])
        data = line.qing_workbench_queue(date_from='2026-09-11')
        self.assertFalse(any(x['id'] == line.id for x in data['lines']))
        with self.assertRaises(UserError):
            line.qing_workbench_queue(date_from='2026-10-01', date_to='2026-09-01')

    def test_restricted_user(self):
        line = self._line()
        user = new_test_user(self.env, login='workbench_billing', groups='account.group_account_invoice')
        for method, args in [('qing_workbench_queue', []), ('qing_workbench_candidates', []),
                             ('qing_workbench_match', [1, 'invalid'])]:
            with self.assertRaises(AccessError):
                getattr(line.with_user(user), method)(*args)

    def test_cross_company_rejected(self):
        line = self._line()
        other = self.env['res.company'].create({'name': 'Workbench other'})
        with self.assertRaises(AccessError):
            line.with_company(other).qing_workbench_candidates()
        with self.assertRaises(AccessError):
            line.with_company(other).qing_workbench_queue(journal_id=line.journal_id.id)

    def test_foreign_currency_notice(self):
        foreign = self.env.ref('base.EUR')
        if foreign == self.env.company.currency_id:
            foreign = self.env.ref('base.USD')
        foreign.active = True
        line = self._line(foreign_currency_id=foreign.id, amount_currency=-200)
        self.assertIn('外币', line.qing_workbench_candidates()['notice'])
        self.assertFalse(line.qing_workbench_candidates()['candidates'])

    def test_queue_pagination(self):
        lines = self.env['account.bank.statement.line'].create([{
            'journal_id': self.company_data['default_journal_bank'].id,
            'date': '2026-09-25', 'amount': -10, 'payment_ref': 'Page %s' % i,
        } for i in range(31)])
        first = lines.qing_workbench_queue(date_from='2026-09-25', date_to='2026-09-25')
        second = lines.qing_workbench_queue(date_from='2026-09-25', date_to='2026-09-25', offset=30)
        self.assertEqual(len(first['lines']), 30)
        self.assertTrue(first['more'])
        self.assertEqual(len(second['lines']), 1)
        self.assertFalse(second['more'])

    def test_manual_edit_delete_and_reset(self):
        line = self._line()
        move = line.move_id
        line.write({'amount': -400, 'payment_ref': 'Manual correction'})
        self.assertEqual(line.amount, -400)
        payment = self._payment(amount=400)
        item = line.qing_workbench_candidates()['candidates'][0]
        line.qing_workbench_match(payment.id, item['token'])
        self.assertTrue(line.qing_edit_locked)
        for values in [{'amount': -500}, {'payment_ref': 'Forbidden'}, {'partner_name': 'Other'},
                       {'qing_bank_reference': 'Changed'}, {'date': '2026-09-11'}]:
            with self.assertRaises(UserError):
                line.write(values)
        with self.assertRaises(UserError):
            line.unlink()
        line.action_qing_undo_match()
        self.assertFalse(line.qing_edit_locked)
        line.write({'payment_ref': 'After Reset'})
        line.unlink()
        self.assertFalse(line.exists())
        self.assertFalse(move.exists())
        self.assertTrue(payment.exists())

    def test_batch_edit_locked_is_atomic(self):
        first, second = self._line(), self._line()
        payment = self._payment()
        item = second.qing_workbench_candidates()['candidates'][0]
        second.qing_workbench_match(payment.id, item['token'])
        with self.assertRaises(UserError):
            (first | second).write({'payment_ref': 'Must not change'})
        self.assertEqual(first.payment_ref, 'Workbench synthetic')
        with self.assertRaises(UserError):
            (first | second).unlink()
        self.assertTrue(first.exists())

    def test_manager_manual_form(self):
        user = new_test_user(self.env, login='bank_manual_manager',
                             groups='account.group_account_manager',
                             company_id=self.env.company.id)
        model = self.env['account.bank.statement.line'].with_user(user)
        with Form(model, view='qing_account.view_bank_transactions_form') as form:
            form.journal_id = self.company_data['default_journal_bank']
            form.date = '2026-09-10'
            form.payment_ref = 'Manual form synthetic'
            form.amount = -100
        line = form.record
        line.write({'amount': -120})
        self.assertEqual(line.amount, -120)
        line.unlink()

    def test_zero_amount_cannot_be_saved(self):
        for amount in [0, 0.001]:
            with self.assertRaisesRegex(UserError, '金额不能为 0'), self.env.cr.savepoint():
                self._line(amount=amount)
        line = self._line()
        with self.assertRaisesRegex(UserError, '金额不能为 0'), self.env.cr.savepoint():
            line.write({'amount': 0})
        self.assertEqual(line.amount, -300)

    def test_legacy_zero_can_be_corrected_or_deleted(self):
        # Simulate records saved before the nonzero validation was introduced.
        model = self.env['account.bank.statement.line']
        checks = [check for check in model._constraint_methods
                  if check.__name__ != '_check_qing_nonzero_amount']
        with patch.object(type(model), '_constraint_methods', checks):
            line = self._line(amount=0)
            obsolete = self._line(amount=0)
        self.assertTrue(line.is_reconciled)  # Native zero-residual status is not a payment match.
        self.assertFalse(line.qing_edit_locked)
        self.assertFalse(line.qing_matched_payment_id)
        line.write({'amount': -300})
        self.assertFalse(line.is_reconciled)
        payment = self._payment()
        item = line.qing_workbench_candidates()['candidates'][0]
        line.qing_workbench_match(payment.id, item['token'])
        self.assertTrue(line.qing_edit_locked)
        line.action_qing_undo_match()
        self.assertFalse(line.qing_edit_locked)
        obsolete.unlink()
        self.assertFalse(obsolete.exists())
