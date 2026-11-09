from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestJournalChineseAliases(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Journal = cls.env['account.journal']
        cls.company = cls.env.company

    def _cash_values(self, name, **extra):
        return {'name': name, 'type': 'cash', 'company_id': self.company.id, **extra}

    def test_three_chinese_cash_journals_keep_unique_aliases(self):
        base_alias = self.Journal._alias_prepare_alias_name(
            False, '现金', False, 'cash', self.company)
        self.Journal.create(self._cash_values('Existing cash alias', alias_name=base_alias))
        journals = self.Journal.browse()
        for name in ('现金 - 家具店', '现金 - 服装店', '现金 - 面包店'):
            journals |= self.Journal.create(self._cash_values(name))
        self.assertEqual(len(set(journals.mapped('code'))), 3)
        self.assertEqual(len(set(journals.mapped('alias_name'))), 3)
        self.assertTrue(all('-none' not in name for name in journals.mapped('alias_name')))

    def test_chinese_cash_batch_reserves_later_explicit_code(self):
        reserved = self.Journal._get_next_journal_default_code('cash', self.company)
        values = [self._cash_values('批量现金甲'),
                  self._cash_values('批量现金乙', code=reserved),
                  self._cash_values('批量现金丙')]
        original = [dict(value) for value in values]
        journals = self.Journal.create(values)
        self.assertEqual(journals[1].code, reserved)
        self.assertEqual(len(set(journals.mapped('code'))), 3)
        self.assertEqual(len(set(journals.mapped('alias_name'))), 3)
        self.assertEqual(values, original)

    def test_chinese_bank_batch(self):
        journals = self.Journal.create([
            {'name': name, 'type': 'bank', 'company_id': self.company.id}
            for name in ('中国银行演示一', '中国银行演示二')])
        self.assertEqual(len(set(journals.mapped('code'))), 2)
        self.assertEqual(len(set(journals.mapped('alias_name'))), 2)

    def test_explicit_code_is_preserved(self):
        journal = self.Journal.create(self._cash_values('指定现金代码', code='QC91'))
        self.assertEqual(journal.code, 'QC91')

    def test_explicit_alias_is_preserved(self):
        journal = self.Journal.create(self._cash_values('指定收款地址', alias_name='qing-cash-explicit'))
        self.assertTrue(journal.alias_name.startswith('qing-cash-explicit'))

    def test_disabled_alias_remains_disabled(self):
        journal = self.Journal.create(self._cash_values('不使用邮件收款', alias_name=False))
        self.assertFalse(journal.alias_name)

    def test_english_name_uses_native_alias(self):
        name = 'Qing unique English cash journal'
        expected = self.Journal._alias_prepare_alias_name(False, name, False, 'cash', self.company)
        expected_code = self.Journal._get_next_journal_default_code('cash', self.company)
        journal = self.Journal.create(self._cash_values(name))
        self.assertEqual(journal.alias_name, expected)
        self.assertEqual(journal.code, expected_code)

    def test_company_codes_are_allocated_independently(self):
        other_company = self.setup_other_company()['company']
        journals = self.Journal.create([
            self._cash_values('本公司现金'),
            self._cash_values('另一公司现金', company_id=other_company.id),
        ])
        self.assertEqual(journals.company_id, self.company | other_company)
        self.assertEqual(len(set(journals.mapped('alias_name'))), 2)
