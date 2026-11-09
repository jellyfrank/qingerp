from unittest.mock import patch

from lxml import html

from odoo import Command
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import UserError


@tagged('post_install', '-at_install')
class TestChinaVoucher(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env['res.company'].with_context(chart_template_load=True).create({
            'name': '凭证测试公司', 'country_id': cls.env.ref('base.cn').id,
            'currency_id': cls.env.ref('base.CNY').id,
        })
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.company = company.with_env(cls.env)
        cls.env['account.chart.template'].try_loading('cn', cls.company, install_demo=False)
        cls.report = cls.env.ref('l10n_cn.account_voucher_cn')
        cls.journal = cls.env['account.journal'].search([
            ('company_id', '=', company.id), ('type', '=', 'general'),
        ], limit=1)
        cls.accounts = cls.env['account.account'].search([
            ('company_ids', 'in', company.ids), ('account_type', '=', 'asset_current'),
        ], limit=2)

    def _move(self, currency=None):
        currency = currency or self.company.currency_id
        return self.env['account.move'].create({
            'journal_id': self.journal.id, 'date': '2026-09-01',
            'currency_id': currency.id,
            'line_ids': [Command.create({
                'name': '期初测试摘要', 'account_id': account.id,
                'currency_id': currency.id,
                'amount_currency': sign * (100 if currency != self.company.currency_id else 700),
                'debit': 700 if sign == 1 else 0,
                'credit': 700 if sign == -1 else 0,
            }) for account, sign in zip(self.accounts, (1, -1))],
        })

    def _render(self, moves):
        content, _ = self.report._render_qweb_html(self.report.report_name, moves.ids)
        return html.fromstring(content)

    def test_foreign_currency_totals_and_draft(self):
        move = self._move(self.env.ref('base.USD'))
        page = self._render(move)
        self.assertIn('草稿 · 未过账', page.text_content())
        self.assertIn('USD', page.text_content())
        total = page.xpath('//td[@name="total_debit"]')[0].text_content()
        self.assertIn('700.00', total)
        self.assertNotIn('$', total)
        self.assertIn(self.company.currency_id.symbol, total)
        self.assertEqual(move.state, 'draft')
        self.assertEqual(move.amount_total, 100)

    def test_batch_print_and_posted_status(self):
        first, second = self._move(), self._move()
        second.action_post()
        pages = self._render(first | second).xpath('//div[contains(@class,"qing_cn_voucher")]')
        self.assertEqual(len(pages), 2)
        self.assertIn('草稿 · 未过账', pages[0].text_content())
        self.assertNotIn('草稿 · 未过账', pages[1].text_content())
        self.assertEqual(set(self.report.binding_view_types.split(',')), {'form', 'list'})

    def test_amount_words_without_cn2an(self):
        move = self._move()
        with patch.object(type(move), 'check_cn2an', return_value=None):
            self.assertIn('人民币大写：柒佰元整', self._render(move).text_content())

    def test_non_cny_does_not_claim_rmb_words(self):
        # Currency must be set before creating any journal items, including drafts.
        self.company.currency_id = self.env.ref('base.USD')
        move = self._move()
        self.assertNotIn('人民币大写', self._render(move).text_content())

    def test_uninstall_restores_only_our_binding(self):
        from odoo.addons.qing_account_cn.hooks import uninstall_hook

        uninstall_hook(self.env)
        self.assertEqual(self.report.binding_view_types, 'form')
        self.report.binding_view_types = 'list'
        uninstall_hook(self.env)
        self.assertEqual(self.report.binding_view_types, 'list')

    def test_traditional_style_and_digits(self):
        self.company.qing_cn_voucher_style = 'traditional'
        move = self._move()
        page = self._render(move)
        self.assertEqual(len(page.xpath('//table[@name="traditional_entry_table"]')), 1)
        self.assertIn('人民币大写：柒佰元整', page.text_content())
        self.assertEqual(move._qing_cn_voucher_digits(700)['digits'], '      70000')
        self.assertEqual(move._qing_cn_voucher_digits(-0.01)['sign'], '-')
        self.assertEqual(move._qing_cn_voucher_digits(0)['digits'], ' ' * 11)
        with self.assertRaises(UserError):
            move._qing_cn_voucher_digits(1000000000)
        self.company.qing_cn_voucher_style = 'standard'
        self.assertFalse(self._render(move).xpath('//table[@name="traditional_entry_table"]'))

    def test_traditional_pagination(self):
        self.company.qing_cn_voucher_style = 'traditional'
        move = self._move()
        move.line_ids = [Command.create({
            'name': 'Additional zero line', 'account_id': self.accounts[0].id,
        }) for index in range(5)]
        self.assertEqual([len(lines) for lines in move._qing_cn_voucher_pages()], [6, 1])
        self.assertEqual(len(self._render(move).xpath('//table[@name="traditional_entry_table"]')), 2)

    def test_mixed_company_batch_uses_document_style(self):
        first = self._move()
        other = self.env['res.company'].create({
            'name': '传统凭证公司', 'currency_id': self.company.currency_id.id,
            'qing_cn_voucher_style': 'traditional',
        })
        other_env = self.env(context=dict(self.env.context, allowed_company_ids=(self.company | other).ids))
        journal = other_env['account.journal'].create({'name': 'Other general', 'code': 'OTH', 'type': 'general', 'company_id': other.id})
        account = other_env['account.account'].create({'name': 'Other account', 'code': '1001', 'account_type': 'asset_current', 'company_ids': [Command.set(other.ids)]})
        second = other_env['account.move'].with_company(other).create({
            'journal_id': journal.id, 'date': '2026-09-01',
            'line_ids': [Command.create({'name': 'Other sample', 'account_id': account.id, 'debit': debit, 'credit': credit}) for debit, credit in [(10, 0), (0, 10)]],
        })
        report = self.report.with_env(other_env)
        content, _ = report._render_qweb_html(report.report_name, [first.id, second.id])
        root = html.fromstring(content)
        self.assertEqual(len(root.xpath('//table[@name="entry_line_table"]')), 1)
        self.assertEqual(len(root.xpath('//table[@name="traditional_entry_table"]')), 1)
        settings = other_env['res.config.settings'].create({'company_id': other.id})
        self.assertEqual(settings.qing_cn_voucher_style, 'traditional')
        settings.qing_cn_voucher_style = 'standard'
        self.assertEqual(other.qing_cn_voucher_style, 'standard')
        self.assertEqual(self.company.qing_cn_voucher_style, 'standard')
