import io

from openpyxl import load_workbook

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestFinancialReportScheme(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login='report_scheme_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.billing = new_test_user(
            cls.env, login='report_scheme_billing', groups='account.group_account_invoice',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.bank = cls.company_data['default_account_assets']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.closing = cls.env['account.account'].create({
            'code': 'FT3103', 'name': 'Current year profit', 'account_type': 'equity_unaffected',
        })
        cls.balance = cls.env['qing.balance.sheet.mapping'].create({
            'name': 'Existing balance rules',
            'line_ids': [
                Command.create({'account_id': account.id, 'line_code': row})
                for account, row in [(cls.bank, '1'), (cls.revenue, '51'), (cls.closing, '51')]
            ],
        })
        cls.balance.action_confirm()
        cls.profit = cls.env['qing.profit.statement.mapping'].create({
            'name': 'Existing profit rules',
            'closing_account_ids': [Command.set(cls.closing.ids)],
            'line_ids': [Command.create({'account_id': cls.revenue.id, 'line_code': '1'})],
        })
        cls.profit.action_confirm()

    def test_adopts_unique_existing_pair_and_opens_report(self):
        model = self.env['qing.financial.report.scheme'].with_user(self.manager)
        model._bootstrap_existing_companies()
        scheme = model.search([('company_id', '=', self.env.company.id)])
        self.assertEqual(len(scheme), 1)
        self.assertEqual(scheme.source, 'existing')
        self.assertEqual(scheme.balance_mapping_id, self.balance)
        self.assertEqual(scheme.profit_mapping_id, self.profit)
        model._bootstrap_company(self.env.company)
        self.assertEqual(model.search_count([('company_id', '=', self.env.company.id)]), 1)
        service = self.env['qing.financial.report'].with_user(self.manager)
        balance = service.get_report('balance', '2026-09-25')
        profit = service.get_report('profit', '2026-09-25')
        self.assertEqual(balance['status'], 'ready')
        self.assertEqual(profit['status'], 'ready')
        self.assertEqual(len(balance['rows']), 53)
        self.assertEqual(len(profit['rows']), 32)
        if not scheme.cash_account_ids:
            cash = service.get_report('cash', '2026-09-25')
            self.assertEqual(cash['status'], 'incomplete')
            self.assertEqual(cash['headers'], ['本年累计金额', '本月金额'])
            self.assertTrue(cash['empty'])
        xlsx_action = service.export_formatted('balance', '2026-09-25', file_type='xlsx')
        self.assertEqual(xlsx_action['target'], 'download')
        export = self.env['qing.financial.report.export'].search([], order='id desc', limit=1)
        workbook = load_workbook(io.BytesIO(export.file.content), read_only=True)
        self.assertEqual(workbook.worksheets[0]['B6'].value, '货币资金')
        self.assertIn('科目贡献', workbook.sheetnames)
        pdf_action = service.export_formatted('balance', '2026-09-25', file_type='pdf')
        self.assertEqual(pdf_action['type'], 'ir.actions.report')

    def test_ambiguous_pair_requires_administrator_choice(self):
        second = self.balance.copy({'name': 'Alternative rules'})
        second.action_confirm()
        self.env['qing.financial.report.scheme']._bootstrap_company(self.env.company)
        self.assertFalse(self.env['qing.financial.report.scheme'].search([
            ('company_id', '=', self.env.company.id),
        ]))
        result = self.env['qing.financial.report'].with_user(self.manager).get_report(
            'balance', '2026-09-25'
        )
        self.assertEqual(result['status'], 'setup')

    def test_effective_dates_do_not_overlap_and_active_pair_is_immutable(self):
        model = self.env['qing.financial.report.scheme']
        scheme = model.create({
            'name': 'Initial rules', 'date_from': '2025-01-01',
            'balance_mapping_id': self.balance.id, 'profit_mapping_id': self.profit.id,
        })
        scheme.action_activate()
        with self.assertRaises(UserError):
            scheme.write({'balance_mapping_id': self.balance.id})
        overlap = model.create({
            'name': 'Overlapping rules', 'date_from': '2026-01-01',
            'balance_mapping_id': self.balance.id, 'profit_mapping_id': self.profit.id,
        })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            overlap.action_activate()
        scheme.date_to = '2025-12-31'
        overlap.action_activate()
        self.assertEqual(model._resolve(self.env.company, '2025-09-25'), scheme)
        self.assertEqual(model._resolve(self.env.company, '2026-09-25'), overlap)

    def test_billing_user_cannot_read_report(self):
        with self.assertRaises((AccessError, UserError)):
            self.env['qing.financial.report'].with_user(self.billing).get_report(
                'balance', '2026-09-25'
            )


@tagged('post_install', '-at_install')
class TestFinancialReportNativeCN(AccountTestInvoicingCommon):
    chart_template = 'cn'
    country_code = 'CN'

    def test_amount_drilldown_uses_row_and_column_period(self):
        self.env['qing.financial.report.scheme']._bootstrap_company(self.env.company)
        bank = self.env['account.chart.template'].ref('l10n_cn_common_account_1001')
        revenue = self.env['account.chart.template'].ref('l10n_cn_account_5001')
        posted = []
        for when, amount in [('2025-08-10', 50), ('2026-01-10', 20), ('2026-09-10', 75)]:
            move = self.env['account.move'].create({
                'journal_id': self.company_data['default_journal_misc'].id,
                'date': when,
                'line_ids': [
                    Command.create({'account_id': bank.id, 'balance': amount}),
                    Command.create({'account_id': revenue.id, 'balance': -amount}),
                ],
            })
            move.action_post()
            posted.append(move)
        service = self.env['qing.financial.report']
        lines = self.env['account.move.line']

        def source(report_type, row, column):
            action = service.open_row_source(report_type, '2026-09-25', row, column)
            self.assertEqual(action['res_model'], 'account.move.line')
            self.assertEqual(action['context'], {})
            return lines.search(action['domain'])

        self.assertEqual(source('balance', 1, 0), posted[0].line_ids.filtered(
            lambda line: line.account_id == bank,
        ))
        self.assertEqual(set(source('balance', 1, 1).ids), {
            line.id for move in posted for line in move.line_ids if line.account_id == bank
        })
        self.assertEqual(set(source('profit', 1, 0).ids), {
            line.id for move in posted[1:] for line in move.line_ids if line.account_id == revenue
        })
        self.assertEqual(source('profit', 1, 1), posted[2].line_ids.filtered(
            lambda line: line.account_id == revenue,
        ))
        self.assertFalse(source('balance', 2, 1))

    def test_reclassified_trade_drilldown_filters_partner(self):
        self.env['qing.financial.report.scheme']._bootstrap_company(self.env.company)
        receivable = self.env['account.chart.template'].ref('l10n_cn_common_account_1122')
        revenue = self.env['account.chart.template'].ref('l10n_cn_account_5001')
        customer = self.env['res.partner'].create({'name': 'Drilldown customer'})
        advance = self.env['res.partner'].create({'name': 'Drilldown advance'})
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': '2026-09-10',
            'line_ids': [
                Command.create({'account_id': receivable.id, 'balance': 100,
                                'partner_id': customer.id}),
                Command.create({'account_id': receivable.id, 'balance': -40,
                                'partner_id': advance.id}),
                Command.create({'account_id': revenue.id, 'balance': -60}),
            ],
        })
        move.action_post()
        service = self.env['qing.financial.report']
        lines = self.env['account.move.line']
        receivable_action = service.open_row_source('balance', '2026-09-25', 4, 1)
        advance_action = service.open_row_source('balance', '2026-09-25', 34, 1)
        self.assertEqual(lines.search(receivable_action['domain']), move.line_ids.filtered(
            lambda line: line.partner_id == customer,
        ))
        self.assertEqual(lines.search(advance_action['domain']), move.line_ids.filtered(
            lambda line: line.partner_id == advance,
        ))

    def test_native_chart_bootstrap_and_unmapped_exception(self):
        scheme_model = self.env['qing.financial.report.scheme']
        scheme_model._bootstrap_company(self.env.company)
        scheme = scheme_model.search([('company_id', '=', self.env.company.id)])
        self.assertEqual(len(scheme), 1)
        self.assertEqual(scheme.state, 'active')
        self.assertEqual(scheme.source, 'system')
        self.assertGreater(len(scheme.balance_mapping_id.line_ids), 100)
        scheme_model._bootstrap_company(self.env.company)
        self.assertEqual(scheme_model.search_count([('company_id', '=', self.env.company.id)]), 1)
        report = self.env['qing.financial.report'].get_report('balance', '2026-09-25')
        self.assertEqual(report['status'], 'ready')
        custom = self.env['account.account'].create({
            'code': 'FT9999', 'name': 'Custom asset', 'account_type': 'asset_current',
        })
        bank = self.env['account.chart.template'].ref('l10n_cn_common_account_1001')
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': '2026-09-01',
            'line_ids': [
                Command.create({'account_id': custom.id, 'balance': 50}),
                Command.create({'account_id': bank.id, 'balance': -50}),
            ],
        })
        move.action_post()
        incomplete = self.env['qing.financial.report'].get_report('balance', '2026-09-25')
        self.assertEqual(incomplete['status'], 'incomplete')
        self.assertTrue(any('Unmapped account' in issue for issue in incomplete['issues']))
        diagnostic = self.env['qing.financial.report'].export_diagnostics('balance', '2026-09-25')
        self.assertEqual(diagnostic['target'], 'download')
        with self.assertRaises(UserError):
            self.env['qing.financial.report'].export_csv('balance', '2026-09-25')
