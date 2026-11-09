from psycopg2 import IntegrityError

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestProfitStatement(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env,
            login='profit_manager',
            groups='account.group_account_manager',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.billing = new_test_user(
            cls.env, login='profit_billing', groups='account.group_account_invoice'
        )
        cls.bank = cls.company_data['default_account_assets']
        cls.payable = cls.company_data['default_account_payable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.cost = cls.company_data['default_account_expense']
        cls.sales_expense = cls.env['account.account'].create({
            'code': 'PF5601', 'name': 'Sales expense', 'account_type': 'expense',
        })
        cls.interest = cls.env['account.account'].create({
            'code': 'PF5603', 'name': 'Interest expense', 'account_type': 'expense',
        })
        cls.investment = cls.env['account.account'].create({
            'code': 'PF5111', 'name': 'Investment income', 'account_type': 'income',
        })
        cls.non_operating_income = cls.env['account.account'].create({
            'code': 'PF5301', 'name': 'Non-operating income', 'account_type': 'income_other',
        })
        cls.non_operating_expense = cls.env['account.account'].create({
            'code': 'PF5711', 'name': 'Non-operating expense', 'account_type': 'expense',
        })
        cls.income_tax = cls.env['account.account'].create({
            'code': 'PF5801', 'name': 'Income tax', 'account_type': 'expense',
        })
        cls.capital = cls.env['account.account'].create({
            'code': 'PF3001', 'name': 'Capital', 'account_type': 'equity',
        })
        cls.profit = cls.env['account.account'].create({
            'code': 'PF3103', 'name': 'Current year profit', 'account_type': 'equity_unaffected',
        })
        cls.balance_mapping = cls.env['qing.balance.sheet.mapping'].with_user(cls.manager).create({
            'name': 'Profit test balance mapping',
            'line_ids': [
                Command.create({'account_id': account.id, 'line_code': str(row)})
                for account, row in [
                    (cls.bank, 1), (cls.payable, 33), (cls.capital, 48),
                    (cls.profit, 51), (cls.revenue, 51), (cls.cost, 51),
                    (cls.sales_expense, 51), (cls.interest, 51), (cls.investment, 51),
                    (cls.non_operating_income, 51), (cls.non_operating_expense, 51),
                    (cls.income_tax, 51),
                ]
            ],
        })
        cls.balance_mapping.action_confirm()
        cls.mapping = cls.env['qing.profit.statement.mapping'].with_user(cls.manager).create({
            'name': 'Profit test mapping',
            'closing_account_ids': [Command.set(cls.profit.ids)],
            'line_ids': [
                Command.create({'account_id': account.id, 'line_code': str(row)})
                for account, row in [
                    (cls.revenue, 1), (cls.cost, 2), (cls.sales_expense, 11),
                    (cls.interest, 19), (cls.investment, 20),
                    (cls.non_operating_income, 22), (cls.non_operating_expense, 24),
                    (cls.income_tax, 31),
                ]
            ],
        })
        cls.mapping.action_confirm()

    def _move(self, entries, date='2026-09-01', posted=True, reference=False):
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': date,
            'ref': reference,
            'line_ids': [
                Command.create({'account_id': account.id, 'balance': amount})
                for account, amount in entries
            ],
        })
        if posted:
            move.action_post()
        return move

    def _wizard(self, **values):
        return self.env['qing.profit.statement'].with_user(self.manager).create({
            'balance_mapping_id': self.balance_mapping.id,
            'date_to': '2026-09-30',
            'mapping_id': self.mapping.id,
            **values,
        })

    def test_monthly_ytd_formulas_and_boundaries(self):
        self._move([(self.bank, 100), (self.revenue, -100)], '2026-01-10')
        current_sale = self._move([(self.bank, 200), (self.revenue, -200)])
        self._move([(self.cost, 80), (self.bank, -80)])
        self._move([(self.sales_expense, 20), (self.bank, -20)])
        self._move([(self.bank, 10), (self.investment, -10)])
        self._move([(self.income_tax, 5), (self.bank, -5)])
        self._move([(self.bank, 999), (self.revenue, -999)], '2026-10-01')
        self._move([(self.bank, 888), (self.revenue, -888)], posted=False)
        before = current_sale.line_ids.read(['balance', 'write_date'])
        wizard = self._wizard()
        data = wizard._report_data()
        self.assertEqual(data['amounts'][1], [300, 200])
        self.assertEqual(data['amounts'][2], [80, 80])
        self.assertEqual(data['amounts'][11], [20, 20])
        self.assertEqual(data['amounts'][20], [10, 10])
        self.assertEqual(data['amounts'][21], [210, 110])
        self.assertEqual(data['amounts'][32], [205, 105])
        self.assertEqual(data['bridge_difference'], 0)
        self.assertFalse(data['issues'])
        wizard.action_generate()
        self.assertTrue(wizard.result_file)
        self.assertIn('本年累计金额', wizard.result_file.content.decode('utf-8-sig'))
        self.assertEqual(before, current_sale.line_ids.read(['balance', 'write_date']))

    def test_annual_comparison_and_year_end_requirement(self):
        self._move([(self.bank, 100), (self.revenue, -100)], '2025-06-30')
        self._move([(self.bank, 220), (self.revenue, -220)], '2026-06-30')
        wizard = self._wizard(report_mode='annual', date_to='2026-12-31')
        data = wizard._report_data()
        self.assertEqual(data['amounts'][1], [220, 100])
        self.assertFalse(data['issues'])
        wizard.action_generate()
        self.assertIn('上年金额', wizard.result_file.content.decode('utf-8-sig'))
        with self.assertRaises(UserError):
            self._wizard(report_mode='annual')._report_data()

    def test_earnings_closing_is_excluded_and_auditable(self):
        sale = self._move([(self.bank, 100), (self.revenue, -100)])
        cost = self._move([(self.cost, 30), (self.bank, -30)])
        closing = self._move(
            [(self.revenue, 100), (self.cost, -30), (self.profit, -70)],
            reference='September closing',
        )
        data = self._wizard()._report_data()
        self.assertEqual(data['amounts'][1], [100, 100])
        self.assertEqual(data['amounts'][2], [30, 30])
        self.assertEqual(data['amounts'][32], [70, 70])
        self.assertEqual([move['id'] for move in data['closing_moves']], [closing.id])
        self.assertEqual(data['bridge_difference'], 0)
        wizard = self._wizard(source_account_id=self.revenue.id)
        source_lines = self.env['account.move.line'].search(wizard.action_open_lines()['domain'])
        self.assertEqual(source_lines, sale.line_ids.filtered(lambda line: line.account_id == self.revenue))
        closing_moves = self.env['account.move'].search(wizard.action_open_closing_entries()['domain'])
        self.assertEqual(closing_moves, closing)

    def test_non_closing_equity_entry_remains_outside_exclusion(self):
        self._move([(self.bank, 100), (self.revenue, -100)])
        distribution = self._move([(self.profit, 20), (self.payable, -20)])
        data = self._wizard()._report_data()
        self.assertEqual(data['amounts'][32], [100, 100])
        self.assertFalse(data['closing_moves'])
        self.assertEqual(data['bridge_difference'], -20)
        self.assertTrue(data['warnings'])
        self.assertFalse(data['issues'])
        self.assertNotIn(distribution.id, data['scope_closing_ids'][0])

    def test_mixed_balance_sheet_move_is_not_classified_as_closing(self):
        mixed = self._move([(self.revenue, 100), (self.profit, -70), (self.bank, -30)])
        data = self._wizard()._report_data()
        self.assertEqual(data['amounts'][1], [-100, -100])
        self.assertFalse(data['closing_moves'])
        self.assertNotIn(mixed.id, data['scope_closing_ids'][0])

    def test_disclosure_rows_roll_up_once(self):
        accounts = {}
        for code, row, account_type in [
            ('tax', 4, 'expense'), ('repair', 12, 'expense'),
            ('startup', 15, 'expense'), ('subsidy', 23, 'income_other'),
            ('baddebt', 25, 'expense'),
        ]:
            accounts[code] = self.env['account.account'].create({
                'code': 'PFD' + str(row), 'name': code, 'account_type': account_type,
            })
        mapping = self.mapping.copy()
        mapping.line_ids = [
            Command.create({'account_id': accounts[code].id, 'line_code': str(row)})
            for code, row in [('tax', 4), ('repair', 12), ('startup', 15),
                              ('subsidy', 23), ('baddebt', 25)]
        ]
        mapping.action_confirm()
        balance_mapping = self.balance_mapping.copy()
        balance_mapping.line_ids = [
            Command.create({'account_id': account.id, 'line_code': '51'})
            for account in accounts.values()
        ]
        balance_mapping.action_confirm()
        self._move([
            (accounts['tax'], 4), (accounts['repair'], 5), (accounts['startup'], 6),
            (accounts['subsidy'], -9), (accounts['baddebt'], 8), (self.bank, -14),
        ])
        data = self._wizard(
            mapping_id=mapping.id, balance_mapping_id=balance_mapping.id
        )._report_data()
        for row, expected in [(3, 4), (11, 5), (14, 6), (22, 9), (24, 8)]:
            self.assertEqual(data['amounts'][row], [expected, expected])
        self.assertEqual(data['amounts'][32], [-14, -14])
        self.assertFalse(data['issues'])

    def test_unmapped_profit_blocks_but_capitalized_cost_does_not(self):
        unknown = self.env['account.account'].create({
            'code': 'PF5999', 'name': '=Unknown<script>', 'account_type': 'expense',
        })
        self._move([(unknown, 10), (self.bank, -10)])
        wizard = self._wizard()
        wizard.action_generate()
        self.assertFalse(wizard.result_file)
        self.assertIn('Unmapped profit or loss account', wizard.result)
        self.assertNotIn('<script>', wizard.result)
        capitalized = self.env['account.account'].create({
            'code': 'PF4001', 'name': 'Production cost', 'account_type': 'expense_direct_cost',
        })
        balance_mapping = self.balance_mapping.copy()
        balance_mapping.line_ids = [
            Command.create({'account_id': unknown.id, 'line_code': '51'}),
            Command.create({'account_id': capitalized.id, 'line_code': '11'}),
        ]
        balance_mapping.action_confirm()
        self._move([(capitalized, 25), (self.bank, -25)])
        data = self._wizard(balance_mapping_id=balance_mapping.id)._report_data()
        self.assertTrue(any('Unknown' in issue for issue in data['issues']))
        self.assertFalse(any('Production cost' in issue for issue in data['issues']))

    def test_mapping_integrity_and_balance_alignment(self):
        with self.assertRaises(UserError):
            self.mapping.line_ids[0].write({'line_code': '2'})
        with self.assertRaises(UserError):
            self.mapping.write({'state': 'draft'})
        with self.assertRaises(UserError):
            self.mapping.unlink()
        copy = self.mapping.copy()
        self.assertEqual(copy.state, 'draft')
        self.assertEqual(copy.closing_account_ids, self.mapping.closing_account_ids)
        with self.assertRaises(IntegrityError), self.cr.savepoint(), mute_logger('odoo.sql_db'):
            self.env['qing.profit.statement.mapping.line'].create({
                'mapping_id': copy.id, 'account_id': self.revenue.id, 'line_code': '1',
            })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            copy.closing_account_ids |= self.revenue
        bad_balance = self.balance_mapping.copy()
        bad_balance.line_ids.filtered(lambda line: line.account_id == self.revenue).unlink()
        bad_balance.line_ids = [Command.create({'account_id': self.revenue.id, 'line_code': '14'})]
        bad_balance.action_confirm()
        data = self._wizard(balance_mapping_id=bad_balance.id)._report_data()
        self.assertTrue(any('row 51' in issue for issue in data['issues']))

    def test_permissions_record_rules_and_company(self):
        with self.assertRaises(AccessError):
            self.env['qing.profit.statement'].with_user(self.billing).create({
                'balance_mapping_id': self.balance_mapping.id,
                'mapping_id': self.mapping.id,
            })
        self._move([(self.bank, 40), (self.revenue, -40)])
        self.env['ir.access'].sudo().create({
            'name': 'Test profit visibility',
            'model_id': self.env['ir.model']._get_id('account.move.line'),
            'operation': 'r',
            'domain': "[('account_id', '!=', %s)]" % self.revenue.id,
        })
        wizard = self._wizard()
        wizard.action_generate()
        self.assertFalse(wizard.result_file)
        self.assertIn('accessible ledger is not balanced', wizard.result)
        other = self.setup_other_company(name='Profit other company')['company']
        self.manager.company_ids = [Command.link(other.id)]
        with self.assertRaises(UserError):
            wizard.with_company(other)._report_data()

    def test_scope_change_archived_account_and_foreign_currency(self):
        currency = self.env.ref('base.EUR')
        currency.active = True
        move = self._move([(self.bank, 700), (self.revenue, -700)], posted=False)
        move.line_ids.filtered(lambda line: line.account_id == self.bank).write({
            'currency_id': currency.id, 'amount_currency': 100, 'balance': 700,
        })
        move.action_post()
        self.revenue.active = False
        wizard = self._wizard()
        data = wizard._report_data()
        self.assertEqual(data['amounts'][1], [700, 700])
        wizard.action_generate()
        self.assertTrue(wizard.result_file)
        wizard.write({'date_to': '2026-08-31'})
        self.assertFalse(wizard.result_file)

    def test_chinese_form_and_source_scope(self):
        january = self._move([(self.bank, 10), (self.revenue, -10)], '2026-01-05')
        september = self._move([(self.bank, 20), (self.revenue, -20)])
        self.env['res.lang']._activate_lang('zh_CN')
        with Form(self.env['qing.profit.statement'].with_user(self.manager).with_context(lang='zh_CN')) as form:
            form.date_to = '2026-09-30'
            form.mapping_id = self.mapping
            form.balance_mapping_id = self.balance_mapping
        wizard = form.record
        wizard.action_generate()
        self.assertIn('利润表', wizard.display_name)
        self.assertIn('利润表', wizard.result)
        self.assertIn('本年累计金额', wizard.result_file.content.decode('utf-8-sig'))
        wizard.source_scope = 'secondary'
        lines = self.env['account.move.line'].search(wizard.action_open_lines()['domain'])
        self.assertIn(september.line_ids.filtered(lambda line: line.account_id == self.revenue), lines)
        self.assertNotIn(january.line_ids.filtered(lambda line: line.account_id == self.revenue), lines)


@tagged('post_install', '-at_install')
class TestProfitStatementCN(AccountTestInvoicingCommon):
    chart_template = 'cn'
    country_code = 'CN'

    def test_native_chart_suggestions_use_template_identity(self):
        chart = self.env['account.chart.template'].with_company(self.env.company)
        mapping = self.env['qing.profit.statement.mapping'].create({'name': 'Native profit review'})
        revenue = chart.ref('l10n_cn_account_5001')
        consumption_tax = chart.ref('l10n_cn_account_540301')
        government = chart.ref('l10n_cn_account_530101')
        current_profit = chart.ref('l10n_cn_account_3103')
        distribution = chart.ref('l10n_cn_account_3104')
        revenue.code = 'CUSTOM-REVENUE'
        mapping.action_suggest_cn()
        rows = {line.account_id.id: line.line_code for line in mapping.line_ids}
        self.assertEqual(rows[revenue.id], '1')
        self.assertEqual(rows[consumption_tax.id], '4')
        self.assertEqual(rows[government.id], '23')
        self.assertEqual(mapping.closing_account_ids, current_profit | distribution)
        count = len(mapping.line_ids)
        mapping.action_suggest_cn()
        self.assertEqual(len(mapping.line_ids), count)
        custom = self.env['account.account'].create({
            'code': '500199', 'name': 'Custom income', 'account_type': 'income',
        })
        self.assertNotIn(custom, mapping.line_ids.account_id)
        mapping.action_confirm()
