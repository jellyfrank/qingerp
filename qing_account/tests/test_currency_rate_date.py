from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCurrencyRateDate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.currency = cls.env['res.currency'].create({
            'name': 'QFX', 'symbol': 'Q', 'rounding': 0.01})
        cls.company = cls.env['res.company'].create({'name': 'Rate date synthetic'})
        cls.other = cls.env['res.company'].create({'name': 'Other rate synthetic'})
        cls.rates = cls.env['res.currency.rate']
        cls.rates.create([
            {'currency_id': cls.currency.id, 'company_id': cls.company.id,
             'name': date, 'rate': rate}
            for date, rate in [('2026-08-01', 0.1), ('2026-08-10', 0.2), ('2026-08-11', 0.3)]
        ])

    def _rate(self, date, company=None, all_currencies=False):
        currencies = self.env['res.currency'] if all_currencies else self.currency
        return currencies._get_rates(company or self.company, date)[self.currency.id]

    def test_date_boundaries_and_fallback(self):
        for date, rate, selected in [
            ('2026-08-09', 0.1, '2026-08-01'),
            ('2026-08-10', 0.2, '2026-08-10'),
            ('2026-08-11', 0.3, '2026-08-11'),
            ('2026-08-12', 0.3, '2026-08-11'),
            # Preserve native earliest-rate fallback when no earlier rate exists.
            ('2026-07-01', 0.1, '2026-08-01'),
        ]:
            for value in (date, fields.Date.to_date(date)):
                with self.subTest(date=value):
                    self.assertEqual(self._rate(value), (rate, fields.Date.to_date(selected)))

    def test_company_priority_and_branch(self):
        self.rates.create({'currency_id': self.currency.id, 'company_id': False,
                           'name': '2026-08-10', 'rate': 0.4})
        self.assertEqual(self._rate('2026-08-10')[0], 0.2)
        self.assertEqual(self._rate('2026-08-10', self.other)[0], 0.4)
        branch = self.env['res.company'].create({'name': 'Rate branch', 'parent_id': self.company.id})
        self.assertEqual(self._rate('2026-08-10', branch)[0], 0.2)

    def test_cache_invalidation(self):
        self.assertEqual(self._rate('2026-08-10', all_currencies=True)[0], 0.2)
        record = self.rates.search([('currency_id', '=', self.currency.id),
            ('company_id', '=', self.company.id), ('name', '=', '2026-08-10')])
        record.rate = 0.25
        self.assertEqual(self._rate('2026-08-10', all_currencies=True)[0], 0.25)
        record.unlink()
        self.assertEqual(self._rate('2026-08-10', all_currencies=True)[0], 0.1)
        self.rates.create({'currency_id': self.currency.id, 'company_id': self.company.id,
                           'name': '2026-08-10', 'rate': 0.2})
        self.assertEqual(self._rate('2026-08-10', all_currencies=True)[0], 0.2)

    def test_no_rates_retains_native_default(self):
        self.assertEqual(self._rate('2026-08-10', self.other), (1.0, None))
