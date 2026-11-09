import csv
import io

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestTrialBalance(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='trial_manager', groups='account.group_account_manager',
                                    company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='trial_billing', groups='account.group_account_invoice')
        cls.asset = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']

    def _move(self, amount, date, posted=True, currency=None):
        values = {'account_id': self.asset.id, 'partner_id': self.partner_a.id, 'balance': amount}
        if currency:
            values.update(currency_id=currency.id, amount_currency=amount / 7)
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id, 'date': date,
            'line_ids': [Command.create(values), Command.create({'account_id': self.revenue.id, 'balance': -amount})],
        })
        if posted:
            move.action_post()
        return move

    def _wizard(self, **values):
        return self.env['qing.trial.balance'].with_user(self.manager).create({
            'date_from': '2026-09-01', 'date_to': '2026-09-30', **values})

    def test_opening_period_cutoff_and_readonly(self):
        moves = self._move(100, '2026-08-31') | self._move(50, '2026-09-01')
        moves |= self._move(-20, '2026-09-30') | self._move(999, '2026-10-01')
        moves |= self._move(888, '2026-09-15', posted=False)
        before = moves.line_ids.read(['balance', 'debit', 'credit', 'write_date'])
        wizard = self._wizard()
        data = wizard._report_data()
        row = next(row for row in data['rows'] if row['account_id'] == self.asset.id)
        self.assertEqual([row[key] for key in ('opening_debit', 'opening_credit', 'debit', 'credit', 'closing_debit')],
                         [100, 0, 50, 20, 130])
        self.assertEqual(data['differences'], [0, 0, 0])
        wizard.action_generate()
        self.assertEqual(before, moves.line_ids.read(['balance', 'debit', 'credit', 'write_date']))
        action = wizard.action_open_lines()
        lines = self.env['account.move.line'].search(action['domain'])
        self.assertEqual(set(lines.move_id.ids), set(moves[:3].ids))

    def test_export_scope_and_invalidation(self):
        self._move(70, '2026-09-01')
        wizard = self._wizard()
        wizard.action_generate()
        content = wizard.result_file.content.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(rows[1][1:3], ['2026-09-01', '2026-09-30'])
        self.assertIn('70.00', content)
        wizard.write({'date_to': '2026-09-29'})
        self.assertFalse(wizard.result)
        self.assertFalse(wizard.result_file)

    def test_invalid_scope_and_permissions(self):
        wizard = self._wizard(date_from='2026-10-01')
        with self.assertRaises(UserError):
            wizard.action_generate()
        with self.assertRaises(AccessError):
            self.env['qing.trial.balance'].with_user(self.billing).create({})
        other = self.env['res.company'].create({'name': 'Trial isolated company'})
        wizard = self._wizard()
        with self.assertRaises((AccessError, UserError)):
            wizard.with_company(other).action_generate()

    def test_empty_form_and_escaped_names(self):
        with Form(self.env['qing.trial.balance'].with_user(self.manager)) as form:
            form.date_from = '2020-01-01'
            form.date_to = '2020-01-31'
        wizard = form.record
        self.assertFalse(wizard._report_data()['rows'])
        wizard.action_generate()
        self.assertTrue(wizard.result_file)
        self.asset.name = '=1+1<script>alert(1)</script>'
        self._move(50, '2026-09-01')
        wizard = self._wizard()
        wizard.action_generate()
        self.assertNotIn('<script>', wizard.result)
        self.assertIn("'=1+1", wizard.result_file.content.decode('utf-8-sig'))

    def test_foreign_currency_is_not_summed_as_company_currency(self):
        currency = self.env.ref('base.EUR')
        if currency == self.env.company.currency_id:
            currency = self.env.ref('base.USD')
        currency.active = True
        self._move(700, '2026-09-01', currency=currency)
        data = self._wizard()._report_data()
        self.assertEqual(data['totals']['debit'], 700)
        self.assertEqual(data['totals']['closing_debit'], 700)

    def test_storno_signed_turnover(self):
        self.env.company.account_storno = True
        original = self._move(100, '2026-09-01')
        reversal = original._reverse_moves(default_values_list=[{'date': '2026-09-02'}], cancel=True)
        self.assertEqual(reversal.state, 'posted')
        data = self._wizard()._report_data()
        self.assertEqual(data['totals']['debit'], 0)
        self.assertEqual(data['totals']['credit'], 0)
        self.assertEqual(data['differences'], [0, 0, 0])

    def test_other_company_entries_excluded(self):
        other = self.setup_other_company(name='Trial other company')
        journal = other['default_journal_misc']
        company = journal.company_id
        move = self.env['account.move'].with_company(company).create({
            'journal_id': journal.id, 'date': '2026-09-01',
            'line_ids': [Command.create({'account_id': other['default_account_expense'].id, 'balance': 500}),
                         Command.create({'account_id': other['default_account_revenue'].id, 'balance': -500})]})
        move.action_post()
        self.manager.company_ids = [Command.link(company.id)]
        self._move(10, '2026-09-01')
        data = self._wizard().with_context(allowed_company_ids=[self.env.company.id, company.id])._report_data()
        self.assertEqual(data['totals']['debit'], 10)

    def test_year_boundary_does_not_invent_earnings_transfer(self):
        self._move(30, '2025-12-31')
        data = self._wizard(date_from='2026-01-01', date_to='2026-01-31')._report_data()
        revenue = next(row for row in data['rows'] if row['account_id'] == self.revenue.id)
        self.assertEqual(revenue['opening_credit'], 30)
        self.assertEqual(revenue['closing_credit'], 30)

    def test_cancelled_and_archived_accounts(self):
        move = self._move(900, '2026-09-01', posted=False)
        move.button_cancel()
        self._move(25, '2026-09-01')
        self.revenue.active = False
        data = self._wizard()._report_data()
        self.assertEqual(data['totals']['debit'], 25)
        self.assertIn(self.revenue.id, [row['account_id'] for row in data['rows']])

    def test_line_record_rules_are_respected(self):
        self._move(40, '2026-09-01')
        self.env['ir.access'].sudo().create({
            'name': 'Test trial balance visibility',
            'model_id': self.env['ir.model']._get_id('account.move.line'), 'operation': 'r',
            'domain': "[('account_id', '!=', %s)]" % self.revenue.id,
        })
        data = self._wizard()._report_data()
        self.assertEqual(data['differences'], [0, 40, 40])
        self.assertNotIn(self.revenue.id, [row['account_id'] for row in data['rows']])

    def test_chinese_runtime_translation(self):
        self.env['res.lang']._activate_lang('zh_CN')
        wizard = self._wizard().with_context(lang='zh_CN')
        self.assertEqual(wizard._headers()[0], '科目编码')
        self.assertEqual(wizard.action_generate()['name'], '科目余额与试算平衡')
        self.assertIn('期初／本期／期末借贷差额', wizard.result)

    def test_full_page_report_payload_preserves_trial_balance_values(self):
        self._move(100, '2026-08-31')
        self._move(50, '2026-09-01')
        self._move(-20, '2026-09-30')
        service = self.env['qing.financial.report'].with_user(self.manager)
        report = service.get_report('trial_balance', '2026-09-30', date_from='2026-09-01')

        self.assertEqual(report['report_type'], 'trial_balance')
        self.assertEqual((report['date_from'], report['date_to']), ('2026-09-01', '2026-09-30'))
        self.assertEqual(report['headers'][2:], [
            'Opening Debit', 'Opening Credit', 'Period Debit', 'Period Credit',
            'Closing Debit', 'Closing Credit',
        ])
        asset_row = next(row for row in report['rows'] if row['code'] == self.asset.code)
        self.assertEqual(asset_row['amounts'], ['100.00', '0.00', '50.00', '20.00', '130.00', '0.00'])
        self.assertEqual(report['differences'], ['0.00', '0.00', '0.00'])

    def test_menu_opens_full_page_and_page_actions_keep_scope(self):
        menu = self.env.ref('qing_account_reports.menu_trial_balance')
        client_action = self.env.ref('qing_account_reports.action_trial_balance_workspace')
        self.assertEqual(menu.action, client_action)
        reporting_menu = self.env.ref('account.menu_finance_reports')
        for menu_xmlid in (
            'qing_account_reports.menu_trial_balance',
            'qing_account_reports.menu_balance_sheet',
            'qing_account_reports.menu_profit_statement',
            'qing_account_reports.menu_cash_flow',
        ):
            self.assertEqual(self.env.ref(menu_xmlid).parent_id, reporting_menu)
        self.assertEqual(client_action.type, 'ir.actions.client')
        self.assertEqual(client_action.tag, 'qing_account_reports.financial_report')
        self.assertEqual(client_action.params, {'report_type': 'trial_balance'})

        move = self._move(70, '2026-09-01')
        service = self.env['qing.financial.report'].with_user(self.manager)
        lines_action = service.open_trial_balance_lines('2026-09-01', '2026-09-30')
        lines = self.env['account.move.line'].search(lines_action['domain'])
        self.assertTrue(lines)
        self.assertTrue(set(move.ids).issubset(lines.move_id.ids))

        download = service.export_csv('trial_balance', '2026-09-30', date_from='2026-09-01')
        self.assertEqual(download['type'], 'ir.actions.act_url')
        self.assertEqual(download['target'], 'download')
        self.assertIn('/web/content/qing.trial.balance/', download['url'])

    def test_full_page_report_keeps_wizard_permission_and_date_validation(self):
        service = self.env['qing.financial.report']
        with self.assertRaises(AccessError):
            service.with_user(self.billing).get_report(
                'trial_balance', '2026-09-30', date_from='2026-10-01',
            )
        with self.assertRaises(UserError):
            service.with_user(self.manager).get_report(
                'trial_balance', '2026-09-30', date_from='2026-10-01',
            )
