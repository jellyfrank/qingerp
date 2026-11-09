from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAccountLedger(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='ledger_manager', groups='account.group_account_manager',
                                    company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.service = cls.env['qing.account.ledger'].with_user(cls.manager)
        cls.account = cls.company_data['default_account_receivable']

    def _move(self, amount, date, posted=True):
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id, 'date': date,
            'line_ids': [Command.create({'account_id': self.account.id, 'partner_id': self.partner_a.id, 'balance': amount}),
                         Command.create({'account_id': self.company_data['default_account_revenue'].id, 'balance': -amount})],
        })
        if posted:
            move.action_post()
        return move

    def _report(self, **kwargs):
        return self.service.get_report(self.account.id, '2026-09-01', '2026-09-30', **kwargs)

    def test_opening_running_same_day_pagination_and_cutoff(self):
        self._move(100, '2026-08-31')
        self._move(50, '2026-09-01')
        self._move(-20, '2026-09-01')
        self._move(-10, '2026-09-30')
        self._move(999, '2026-10-01')
        self._move(888, '2026-09-15', posted=False)
        data = self._report()
        self.assertEqual((data['opening'], data['debit'], data['credit'], data['closing']), (100, 50, 30, 120))
        self.assertEqual([row['running'] for row in data['rows']], [150, 130, 120])
        page = self._report(offset=1, limit=1)
        self.assertEqual(page['count'], 3)
        self.assertEqual(page['rows'][0]['running'], 130)
        self.assertEqual(page['closing'], 120)

    def test_residual_at_cutoff_not_current_residual(self):
        opening = self._move(100, '2026-08-31')
        after = self._move(-100, '2026-10-01')
        (opening | after).line_ids.filtered(lambda line: line.account_id == self.account).reconcile()
        report = self.service.get_report(self.account.id, '2026-08-01', '2026-09-30')
        self.assertEqual(report['rows'][0]['residual'], 100)

    def test_invalid_dates_pages_and_permissions(self):
        for values in ((self.account.id, '2026-10-01', '2026-09-01'), (self.account.id, 'bad', '2026-09-01'), (False, '', '')):
            with self.assertRaises(UserError):
                self.service.get_report(*values)
        with self.assertRaises(UserError):
            self._report(offset=-1)
        with self.assertRaises(UserError):
            self._report(limit=10001)
        billing = new_test_user(self.env, login='ledger_billing', groups='account.group_account_invoice')
        with self.assertRaises(AccessError):
            self.service.with_user(billing).get_accounts()
        with self.assertRaises(AccessError):
            self.service.with_user(billing).export_csv(self.account.id, '2026-09-01', '2026-09-30')

    def test_other_company_account_rejected(self):
        other = self.setup_other_company(name='Ledger isolation')
        self.manager.company_ids = [Command.link(other['company'].id)]
        service = self.service.with_context(allowed_company_ids=[self.env.company.id, other['company'].id])
        with self.assertRaises(UserError):
            service.get_report(other['default_account_receivable'].id, '2026-09-01', '2026-09-30')

    def test_archived_account_and_readonly_export(self):
        move = self._move(25, '2026-09-01')
        self.account.active = False
        before = move.line_ids.read(['balance', 'write_date'])
        self.assertIn(self.account.id, [item['id'] for item in self.service.get_accounts()])
        action = self.service.export_csv(self.account.id, '2026-09-01', '2026-09-30')
        self.assertIn('/web/content/qing.financial.report.export/', action['url'])
        self.assertEqual(before, move.line_ids.read(['balance', 'write_date']))

    def test_line_access_rules_apply_to_opening_totals_and_rows(self):
        self._move(100, '2026-08-31')
        self._move(40, '2026-09-01')
        self._move(-20, '2026-09-02')
        self.env['ir.access'].sudo().create({
            'name': 'Ledger test visibility', 'model_id': self.env['ir.model']._get_id('account.move.line'),
            'operation': 'r', 'domain': "[('balance', '>=', 0)]",
        })
        data = self._report()
        self.assertEqual((data['opening'], data['credit'], data['closing']), (100, 0, 140))
        self.assertEqual(len(data['rows']), 1)
