import io

from openpyxl import load_workbook

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..models.cash_flow_template import ROW_SELECTION


@tagged('post_install', '-at_install')
class TestCashFlow(AccountTestInvoicingCommon):
    chart_template = 'cn'
    country_code = 'CN'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        chart = cls.env['account.chart.template']
        cls.cash = chart.ref('l10n_cn_common_account_1001')
        cls.bank = chart.ref('l10n_cn_common_account_100201')
        cls.receivable = chart.ref('l10n_cn_common_account_1122')
        cls.payable = chart.ref('l10n_cn_common_account_2202')
        cls.loan = chart.ref('l10n_cn_common_account_2001')
        cls.partner = cls.env['res.partner'].create({'name': 'Cash flow test partner'})
        cls.scheme_model = cls.env['qing.financial.report.scheme']
        cls.scheme_model._bootstrap_company(cls.env.company)
        cls.scheme = cls.scheme_model.search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.service = cls.env['qing.financial.report']

    def _post(self, amount, debit, credit, when='2026-09-10'):
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': when,
            'line_ids': [
                Command.create({'account_id': debit.id, 'balance': amount,
                                'partner_id': self.partner.id}),
                Command.create({'account_id': credit.id, 'balance': -amount,
                                'partner_id': self.partner.id}),
            ],
        })
        move.action_post()
        return move

    def test_cash_receipt_payment_loan_and_internal_transfer(self):
        self.assertIn(self.bank, self.scheme.cash_account_ids)
        self._post(100, self.bank, self.receivable)
        self._post(40, self.payable, self.bank)
        self._post(70, self.bank, self.loan)
        self._post(10, self.cash, self.bank)
        data = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(data['status'], 'ready', data['issues'])
        by_code = {row['code']: row['amounts'] for row in data['rows']}
        self.assertEqual(by_code[1], ['100.00', '100.00'])
        self.assertEqual(by_code[3], ['40.00', '40.00'])
        self.assertEqual(by_code[14], ['70.00', '70.00'])
        self.assertEqual(by_code[20], ['130.00', '130.00'])
        self.assertEqual(data['differences'], [0.0, 0.0])
        xlsx = self.service.export_formatted('cash', '2026-09-25', file_type='xlsx')
        self.assertEqual(xlsx['target'], 'download')
        document = self.env['qing.financial.report.export'].search([], order='id desc', limit=1)
        book = load_workbook(io.BytesIO(document.file.content), read_only=True)
        self.assertEqual(book.active['B5'].value, '销售产成品、商品、提供劳务收到的现金')
        self.assertEqual(book.active['C5'].value, 100)
        self.assertEqual(self.service.export_csv('cash', '2026-09-25')['target'], 'download')
        self.assertEqual(self.service.export_formatted('cash', '2026-09-25', file_type='pdf')['type'],
                         'ir.actions.report')
        document = self.env['qing.financial.report.export'].search([], order='id desc', limit=1)
        html, _format = self.env['ir.actions.report']._render_qweb_html(
            'qing_account_reports.report_financial_statement', [document.id],
        )
        self.assertIn('会小企03'.encode(), html)

    def test_unknown_payment_needs_explicit_classification(self):
        self.assertNotIn('21', dict(ROW_SELECTION))
        unknown = self.env['account.account'].create({
            'code': 'CF9999', 'name': 'Unknown clearing', 'account_type': 'asset_current',
        })
        move = self._post(25, self.bank, unknown)
        result = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(len(result['unclassified_lines']), 1)
        self.assertEqual(self.service.export_diagnostics('cash', '2026-09-25')['target'], 'download')
        diagnostic = self.env['qing.financial.report.export'].search([], order='id desc', limit=1)
        self.assertIn('DIAGNOSTIC ONLY', diagnostic.file.content.decode('utf-8-sig'))
        cash_line = move.line_ids.filtered(lambda line: line.account_id == self.bank)
        self.env['qing.cash.flow.allocation'].create({
            'cash_line_id': cash_line.id, 'line_code': '2', 'amount': 25,
        })
        result = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(result['status'], 'ready', (result['issues'], result['rows'][1]))
        self.assertEqual(result['rows'][1]['amounts'], ['25.00', '25.00'])
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['qing.cash.flow.allocation'].create({
                'cash_line_id': cash_line.id, 'line_code': '2', 'amount': 1,
            })

    def test_cash_flow_uses_posted_dates_and_annual_periods(self):
        self._post(17, self.bank, self.receivable, when='2024-11-11')
        self._post(9, self.bank, self.receivable, when='2025-09-10')
        self._post(9, self.bank, self.receivable, when='2026-09-10')
        annual = self.service.get_report('cash', '2025-12-31', report_mode='annual')
        self.assertEqual(annual['status'], 'ready', annual['issues'])
        self.assertEqual(annual['rows'][0]['amounts'], ['9.00', '17.00'])
        month = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(month['rows'][0]['amounts'], ['9.00', '9.00'])

    def test_cash_amount_drilldown_includes_period_and_balance_sources(self):
        previous = self._post(9, self.bank, self.receivable, when='2025-09-10')
        current = self._post(17, self.bank, self.receivable, when='2026-09-10')
        line_model = self.env['account.move.line']

        def source(row, column):
            action = self.service.open_row_source('cash', '2026-09-25', row, column)
            return line_model.search(action['domain'])

        self.assertEqual(source(1, 0), current.line_ids.filtered(
            lambda line: line.account_id == self.bank,
        ))
        self.assertEqual(source(1, 1), source(1, 0))
        self.assertEqual(source(21, 0), previous.line_ids.filtered(
            lambda line: line.account_id == self.bank,
        ))
        self.assertEqual(set(source(22, 0).ids), {
            line.id for move in (previous, current) for line in move.line_ids
            if line.account_id == self.bank
        })

    def test_clearing_partial_reconciliation_and_cross_move_transfer(self):
        clearing = self.env['account.account'].create({
            'code': 'CF9988', 'name': 'Payment clearing',
            'account_type': 'asset_current', 'reconcile': True,
        })
        receipt = self._post(100, self.bank, clearing)
        first = self._post(40, clearing, self.receivable)
        (receipt.line_ids | first.line_ids).filtered(
            lambda line: line.account_id == clearing
        ).reconcile()
        partial = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(partial['status'], 'incomplete')
        self.assertEqual(partial['rows'][0]['amounts'], ['40.00', '40.00'])
        self.assertEqual(partial['unclassified_lines'][0]['amount'], 60)
        second = self._post(60, clearing, self.receivable)
        (receipt.line_ids | second.line_ids).filtered(
            lambda line: line.account_id == clearing
        ).reconcile()
        settled = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(settled['status'], 'ready', settled['issues'])
        self.assertEqual(settled['rows'][0]['amounts'], ['100.00', '100.00'])

        outbound = self._post(30, clearing, self.bank)
        inbound = self._post(30, self.cash, clearing)
        (outbound.line_ids | inbound.line_ids).filtered(
            lambda line: line.account_id == clearing
        ).reconcile()
        transferred = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(transferred['status'], 'ready', transferred['issues'])
        self.assertEqual(transferred['rows'][19]['amounts'], ['100.00', '100.00'])

    def test_direct_sale_vat_and_tax_payment(self):
        chart = self.env['account.chart.template']
        revenue = chart.ref('l10n_cn_account_5001')
        tax = chart.ref('l10n_cn_common_account_22210101')
        sale = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': '2026-09-10',
            'line_ids': [
                Command.create({'account_id': self.bank.id, 'balance': 113}),
                Command.create({'account_id': revenue.id, 'balance': -100}),
                Command.create({'account_id': tax.id, 'balance': -13}),
            ],
        })
        sale.action_post()
        self._post(13, tax, self.bank)
        data = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(data['status'], 'ready', data['issues'])
        self.assertEqual(data['rows'][0]['amounts'], ['113.00', '113.00'])
        self.assertEqual(data['rows'][4]['amounts'], ['13.00', '13.00'])
        balance = self.service.get_report('balance', '2026-09-25')
        profit = self.service.get_report('profit', '2026-09-25')
        self.assertEqual(balance['status'], 'ready', balance['issues'])
        self.assertEqual(profit['status'], 'ready', profit['issues'])
        self.assertEqual(balance['rows'][0]['amounts'][1], '100.00')
        self.assertEqual(profit['rows'][0]['amounts'][0], '100.00')
        self.assertEqual(data['rows'][19]['amounts'][0], '100.00')

    def test_capital_repayment_is_not_guessed_as_profit_distribution(self):
        capital = self.env['account.chart.template'].ref('l10n_cn_account_3001')
        self._post(20, capital, self.bank)
        data = self.service.get_report('cash', '2026-09-25')
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual(data['rows'][17]['amounts'][0], '0.00')
        self.assertEqual(data['unclassified_lines'][0]['amount'], 20)
