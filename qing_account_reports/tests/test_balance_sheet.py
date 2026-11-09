from psycopg2 import IntegrityError

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestBalanceSheet(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='balance_manager', groups='account.group_account_manager',
                                    company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='balance_billing', groups='account.group_account_invoice')
        cls.bank = cls.company_data['default_account_assets']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.payable = cls.company_data['default_account_payable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.expense = cls.company_data['default_account_expense']
        cls.capital = cls.env['account.account'].create({'code': 'F248', 'name': 'Capital', 'account_type': 'equity'})
        cls.profit = cls.env['account.account'].create({'code': 'F251', 'name': 'Retained Profit', 'account_type': 'equity'})
        cls.mapping = cls.env['qing.balance.sheet.mapping'].with_user(cls.manager).create({
            'name': 'Test small business 1', 'line_ids': [Command.create({'account_id': account.id, 'line_code': str(row)})
                for account, row in [(cls.bank, 1), (cls.receivable, 4), (cls.payable, 33),
                                     (cls.capital, 48), (cls.profit, 51), (cls.revenue, 51), (cls.expense, 51)]]})
        cls.mapping.action_confirm()

    def _move(self, entries, date='2026-09-01', posted=True):
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id, 'date': date,
            'line_ids': [Command.create({'account_id': account.id, 'balance': amount, 'partner_id': partner.id if partner else False})
                         for account, amount, partner in entries]})
        if posted:
            move.action_post()
        return move

    def _wizard(self, **values):
        return self.env['qing.balance.sheet'].with_user(self.manager).create({
            'date_to': '2026-09-30', 'mapping_id': self.mapping.id, **values})

    def test_opening_cutoff_equation_and_no_ledger_writes(self):
        opening = self._move([(self.bank, 1000, False), (self.capital, -1000, False)], '2025-12-31')
        sale = self._move([(self.receivable, 200, self.partner_a), (self.revenue, -200, False)])
        self._move([(self.bank, 999, False), (self.revenue, -999, False)], '2026-10-01')
        self._move([(self.bank, 888, False), (self.revenue, -888, False)], posted=False)
        before = (opening | sale).line_ids.read(['balance', 'write_date'])
        wizard = self._wizard()
        data = wizard._report_data()
        self.assertEqual(data['amounts'][30], [1000, 1200])
        self.assertEqual(data['amounts'][51], [0, 200])
        self.assertEqual(data['differences'], [0, 0])
        self.assertFalse(data['issues'])
        wizard.action_generate()
        self.assertTrue(wizard.result_file)
        self.assertEqual(before, (opening | sale).line_ids.read(['balance', 'write_date']))
        self.assertEqual(str(data['opening_date']), '2025-12-31')

    def test_trade_balances_do_not_net_across_partners(self):
        self._move([(self.receivable, 100, self.partner_a), (self.receivable, -40, self.partner_b),
                    (self.payable, -80, self.partner_a), (self.payable, 30, self.partner_b), (self.bank, -10, False)])
        data = self._wizard()._report_data()
        for row, value in [(4, 100), (34, 40), (33, 80), (5, 30)]:
            self.assertEqual(data['amounts'][row][1], value)
        self.assertFalse(data['issues'])
        self.assertEqual(data['differences'], [0, 0])

    def test_prepaid_and_advance_reverse_balances(self):
        prepaid = self.env['account.account'].create({'code': 'F25', 'name': 'Prepaid', 'account_type': 'asset_prepayments'})
        advance = self.env['account.account'].create({'code': 'F234', 'name': 'Advance', 'account_type': 'liability_current'})
        mapping = self.mapping.copy()
        mapping.line_ids = [Command.create({'account_id': prepaid.id, 'line_code': '5'}),
                            Command.create({'account_id': advance.id, 'line_code': '34'})]
        mapping.action_confirm()
        self._move([(prepaid, -20, self.partner_a), (advance, 10, self.partner_a), (self.bank, 10, False)])
        data = self._wizard(mapping_id=mapping.id)._report_data()
        self.assertEqual(data['amounts'][33][1], 20)
        self.assertEqual(data['amounts'][4][1], 10)
        self.assertFalse(data['issues'])

    def test_earnings_transfer_does_not_double_count(self):
        self._move([(self.bank, 100, False), (self.revenue, -100, False)], '2025-12-31')
        self._move([(self.expense, 30, False), (self.bank, -30, False)])
        before = self._wizard()._report_data()
        self.assertEqual(before['amounts'][51], [100, 70])
        self._move([(self.revenue, 100, False), (self.expense, -30, False), (self.profit, -70, False)])
        after = self._wizard()._report_data()
        self.assertEqual(after['amounts'], before['amounts'])
        self.assertFalse(after['issues'])

    def test_inventory_depreciation_and_negative_tax(self):
        mapping = self.mapping.copy()
        accounts = {}
        for code, row in [('10', '10'), ('11', '11'), ('9', '9'), ('18', '18'), ('19', '19'), ('25a', '25'), ('25b', '25'), ('36', '36')]:
            account = self.env['account.account'].create({'code': 'F2' + code, 'name': code, 'account_type': 'asset_current'})
            accounts[code] = account
            mapping.line_ids = [Command.create({'account_id': account.id, 'line_code': row})]
        mapping.action_confirm()
        entries = [(accounts[code], amount, False) for code, amount in [('10', 20), ('11', 30), ('9', 10),
                    ('18', 500), ('19', -100), ('25a', 50), ('25b', -10), ('36', 5)]]
        entries.append((self.capital, -505, False))
        self._move(entries)
        data = self._wizard(mapping_id=mapping.id)._report_data()
        self.assertEqual(data['amounts'][9][1], 60)
        self.assertEqual(data['amounts'][20][1], 400)
        self.assertEqual(data['amounts'][25][1], 40)
        self.assertEqual(data['amounts'][36][1], -5)
        self.assertEqual(data['amounts'][30][1], 500)
        self.assertFalse(data['issues'])

    def test_unmapped_and_missing_partner_block_download(self):
        unknown = self.env['account.account'].create({'code': 'F299', 'name': '=1+1<script>', 'account_type': 'asset_current'})
        self._move([(unknown, 10, False), (self.revenue, -10, False)])
        self._move([(self.receivable, 5, False), (self.revenue, -5, False)])
        wizard = self._wizard()
        wizard.action_generate()
        self.assertFalse(wizard.result_file)
        self.assertIn('Unmapped account', wizard.result)
        self.assertIn('Partner missing', wizard.result)
        self.assertNotIn('<script>', wizard.result)

    def test_mapping_immutable_duplicate_and_company(self):
        with self.assertRaises(UserError):
            self.mapping.line_ids[0].write({'line_code': '14'})
        with self.assertRaises(UserError):
            self.mapping.write({'state': 'draft'})
        with self.assertRaises(UserError):
            self.mapping.unlink()
        copy = self.mapping.copy()
        self.assertEqual(copy.state, 'draft')
        self.assertEqual(len(copy.line_ids), len(self.mapping.line_ids))
        with self.assertRaises(IntegrityError), self.cr.savepoint(), mute_logger('odoo.sql_db'):
            self.env['qing.balance.sheet.mapping.line'].create({'mapping_id': copy.id, 'account_id': self.bank.id, 'line_code': '1'})
        other = self.setup_other_company(name='Balance isolated company')
        with self.assertRaises((ValidationError, UserError)), self.cr.savepoint():
            copy.line_ids = [Command.create({'account_id': other['default_account_revenue'].id, 'line_code': '51'})]
        with self.assertRaises(UserError):
            copy.action_suggest_cn()

    def test_permissions_and_scope_invalidation(self):
        with self.assertRaises(AccessError):
            self.env['qing.balance.sheet'].with_user(self.billing).create({'mapping_id': self.mapping.id})
        wizard = self._wizard()
        wizard.action_generate()
        self.assertTrue(wizard.result_file)
        wizard.write({'date_to': '2026-08-31'})
        self.assertFalse(wizard.result_file)
        with self.assertRaises(UserError):
            self._wizard(mapping_id=self.mapping.copy().id).action_generate()
        other = self.setup_other_company(name='Balance other company')['company']
        self.manager.company_ids = [Command.link(other.id)]
        with self.assertRaises(UserError):
            wizard.with_company(other).action_generate()

    def test_record_rules_are_not_bypassed(self):
        self._move([(self.bank, 40, False), (self.revenue, -40, False)])
        self.env['ir.access'].sudo().create({'name': 'Test balance visibility',
            'model_id': self.env['ir.model']._get_id('account.move.line'), 'operation': 'r',
            'domain': "[('account_id', '!=', %s)]" % self.revenue.id})
        wizard = self._wizard()
        wizard.action_generate()
        self.assertFalse(wizard.result_file)
        self.assertIn('accessible ledger is not balanced', wizard.result)

    def test_foreign_currency_and_archived_account(self):
        currency = self.env.ref('base.EUR')
        currency.active = True
        move = self._move([(self.bank, 700, False), (self.revenue, -700, False)], posted=False)
        move.line_ids.filtered(lambda line: line.account_id == self.bank).write({
            'currency_id': currency.id, 'amount_currency': 100, 'balance': 700})
        move.action_post()
        self.revenue.active = False
        data = self._wizard()._report_data()
        self.assertEqual(data['amounts'][1][1], 700)
        self.assertEqual(data['amounts'][51][1], 700)
        self.assertFalse(data['issues'])

    def test_other_company_balances_are_excluded(self):
        other = self.setup_other_company(name='Balance excluded company')
        company = other['company']
        move = self.env['account.move'].with_company(company).create({
            'journal_id': other['default_journal_misc'].id, 'date': '2026-09-01',
            'line_ids': [Command.create({'account_id': other['default_account_expense'].id, 'balance': 500}),
                         Command.create({'account_id': other['default_account_revenue'].id, 'balance': -500})]})
        move.action_post()
        self.manager.company_ids = [Command.link(company.id)]
        data = self._wizard().with_context(allowed_company_ids=[self.env.company.id, company.id])._report_data()
        self.assertFalse(data['details'])
        self.assertFalse(data['issues'])

    def test_source_entries_and_chinese_form(self):
        move = self._move([(self.receivable, 100, self.partner_a), (self.revenue, -100, False)])
        self.env['res.lang']._activate_lang('zh_CN')
        with Form(self.env['qing.balance.sheet'].with_user(self.manager).with_context(lang='zh_CN')) as form:
            form.date_to = '2026-09-30'
            form.mapping_id = self.mapping
        wizard = form.record
        wizard.action_generate()
        self.assertIn('资产负债表', wizard.display_name)
        self.assertIn('算术检查通过', wizard.result)
        self.assertIn('资产负债表', wizard.result)
        self.assertIn('年初余额', wizard.result_file.content.decode('utf-8-sig'))
        wizard.source_account_id = self.receivable
        lines = self.env['account.move.line'].search(wizard.action_open_lines()['domain'])
        self.assertEqual(lines, move.line_ids.filtered(lambda line: line.account_id == self.receivable))


@tagged('post_install', '-at_install')
class TestBalanceSheetCN(AccountTestInvoicingCommon):
    chart_template = 'cn'
    country_code = 'CN'

    def test_native_chart_suggestions_and_capitalized_costs(self):
        chart = self.env['account.chart.template'].with_company(self.env.company)
        mapping = self.env['qing.balance.sheet.mapping'].create({'name': 'Native CN review'})
        bank = chart.ref('l10n_cn_common_account_1001')
        capital = chart.ref('l10n_cn_account_3001')
        production = chart.ref('l10n_cn_account_4001')
        development = chart.ref('l10n_cn_account_4301')
        production.code = 'CUSTOM-WIP'
        mapping.action_suggest_cn()
        rows = {line.account_id.id: line.line_code for line in mapping.line_ids}
        self.assertEqual(rows[production.id], '11')
        self.assertEqual(rows[development.id], '26')
        count = len(mapping.line_ids)
        mapping.action_suggest_cn()
        self.assertEqual(len(mapping.line_ids), count)
        mapping.action_confirm()
        move = self.env['account.move'].create({'journal_id': self.company_data['default_journal_misc'].id,
            'date': '2026-09-01', 'line_ids': [Command.create({'account_id': account.id, 'balance': balance})
                for account, balance in [(bank, 880), (capital, -1000), (production, 100), (development, 20)]]})
        move.action_post()
        wizard = self.env['qing.balance.sheet'].create({'mapping_id': mapping.id, 'date_to': '2026-09-30'})
        data = wizard._report_data()
        self.assertEqual(data['amounts'][9][1], 100)
        self.assertEqual(data['amounts'][26][1], 20)
        self.assertEqual(data['amounts'][51][1], 0)
        self.assertEqual(data['amounts'][30][1], 1000)
        self.assertFalse(data['issues'])

    def test_suggestions_preserve_edits_and_do_not_guess_custom_accounts(self):
        chart = self.env['account.chart.template']
        custom = self.env['account.account'].create({'code': '100299', 'name': 'Custom account', 'account_type': 'asset_current'})
        bank = chart.ref('l10n_cn_common_account_1001')
        mapping = self.env['qing.balance.sheet.mapping'].create({'line_ids': [
            Command.create({'account_id': bank.id, 'line_code': '14'})]})
        mapping.action_suggest_cn()
        self.assertEqual(mapping.line_ids.filtered(lambda line: line.account_id == bank).line_code, '14')
        self.assertNotIn(custom, mapping.line_ids.account_id)
        self.assertNotIn(chart.ref('account_journal_payment_debit_account_id'), mapping.line_ids.account_id)
