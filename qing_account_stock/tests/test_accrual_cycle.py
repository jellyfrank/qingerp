import logging
from unittest import SkipTest

from freezegun import freeze_time

from odoo import Command
from odoo.tests import TransactionCase, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestAccrualCycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.env['ir.module.module'].search_count([('name', '=', 'l10n_cn'), ('state', '=', 'installed')]):
            raise SkipTest('CNF fixture requires l10n_cn')
        company = cls.env['res.company'].with_context(chart_template_load=True).create({
            'name': 'CNF cycle synthetic', 'country_id': cls.env.ref('base.cn').id,
            'currency_id': cls.env.ref('base.CNY').id})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.company = company.with_env(cls.env)
        cls.env['account.chart.template'].try_loading('cn', cls.company, install_demo=False)
        cls.env['stock.warehouse'].create({'name': 'Cycle', 'code': 'CYC', 'company_id': company.id})
        cls.vendor = cls.env['res.partner'].create({'name': 'Cycle supplier'})
        cls.liability = cls.env['account.account'].create({'name': 'Cycle accrual', 'code': '299903',
            'account_type': 'liability_current', 'company_ids': [Command.set(company.ids)]})
        cls.product = cls.env['product.product'].create({'name': 'Cycle goods', 'type': 'consu',
            'is_storable': True, 'standard_price': 100, 'purchase_method': 'receive',
            'company_id': company.id, 'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})

    def _accrue(self, po, date):
        wizard = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(po.ids)]})
        batch = self.env['qing.accrual.batch']._from_wizard(wizard)
        batch.action_lock()
        preview = self.env['qing.accrual.preview'].create({'batch_id': batch.id,
            'liability_account_id': self.liability.id})
        values = []
        for row in preview._rows():
            values.extend([Command.create({'account_id': row['debit'].id, 'debit': row['amount'],
                                            'partner_id': self.vendor.id}),
                           Command.create({'account_id': row['credit'].id, 'credit': row['amount'],
                                            'partner_id': self.vendor.id})])
        # Only synthetic rollback tests post proposed entries. No product posting API.
        entry = self.env['account.move'].create({'date': date, 'journal_id': self.company.account_stock_journal_id.id,
            'line_ids': values, 'ref': 'Synthetic accrual'})
        entry.action_post()
        return batch, entry

    def _reverse(self, batch, entry, date):
        reversal = entry._reverse_moves(default_values_list=[{'date': date}], cancel=True)
        self.assertEqual(reversal.state, 'posted')
        self.assertEqual(reversal.reversed_entry_id, entry)
        self.assertAlmostEqual(sum((entry | reversal).line_ids.filtered(
            lambda line: line.account_id == self.liability).mapped('balance')), 0)
        batch.action_cancel()
        self.assertFalse(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)]))

    def _bill(self, po, quantity, date):
        previous = po.invoice_ids
        po.action_create_invoice()
        bill = po.invoice_ids - previous
        bill.invoice_date = date
        bill.invoice_line_ids.write({'quantity': quantity, 'price_unit': 110})
        bill.action_post()
        return bill

    def _close_and_check(self, date, stock, accrual, payable):
        if self.company._action_close_stock_valuation(at_date=date):
            self.company.action_close_stock_valuation(at_date=date, auto_post=True)
        domain = [('company_id', '=', self.company.id), ('parent_state', '=', 'posted'), ('date', '<=', date)]
        lines = self.env['account.move.line'].search(domain)
        balance = lambda account: sum(lines.filtered(lambda line: line.account_id == account).mapped('balance'))
        self.assertAlmostEqual(balance(self.company.account_stock_valuation_id), stock)
        self.assertAlmostEqual(self.product.total_value, stock)
        self.assertAlmostEqual(balance(self.liability), -accrual)
        ap = self.vendor.property_account_payable_id
        self.assertAlmostEqual(balance(ap), -payable)
        expense = sum(lines.filtered(lambda line: line.account_id.account_type in ('expense', 'expense_direct_cost')).mapped('balance'))
        self.assertAlmostEqual(expense, 0)
        for move in lines.move_id:
            self.assertTrue(self.company.currency_id.is_zero(sum(move.line_ids.mapped('balance'))))
        _logger.info('CNF_CYCLE mode=%s method=%s date=%s stock=%s accrual=%s payable=%s expense=%s',
            self.company.inventory_valuation, self.company.cost_method, date, stock, accrual, payable, expense)

    def _cycle(self, mode, method, first_quantity=6):
        self.company.write({'inventory_valuation': mode, 'cost_method': method})
        with freeze_time('2026-08-31 12:00:00'):
            po = self.env['purchase.order'].create({'partner_id': self.vendor.id,
                'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 10,
                    'price_unit': 100, 'tax_ids': [Command.clear()]})]})
            po.button_confirm()
            receipt = po.picking_ids
            receipt.move_ids.quantity = 10
            receipt.move_ids.picked = True
            receipt.button_validate()
            batch, entry = self._accrue(po, '2026-08-31')
            self._close_and_check('2026-08-31', 1000, 1000, 0)
        with freeze_time('2026-09-01 12:00:00'):
            self._reverse(batch, entry, '2026-09-01')
        with freeze_time('2026-09-30 12:00:00'):
            if first_quantity:
                self._bill(po, first_quantity, '2026-09-30')
            remaining = 10 - first_quantity
            if remaining:
                batch, entry = self._accrue(po, '2026-09-30')
            self._close_and_check('2026-09-30', 1000 + first_quantity * 10, remaining * 100, first_quantity * 110)
        with freeze_time('2026-10-01 12:00:00'):
            if remaining:
                self._reverse(batch, entry, '2026-10-01')
        with freeze_time('2026-10-31 12:00:00'):
            if remaining:
                self._bill(po, remaining, '2026-10-31')
            self._close_and_check('2026-10-31', 1100, 0, 1100)
            self.assertFalse(self.env['qing.accrual.source'].search([]))

    def test_periodic_average_partial(self):
        self._cycle('periodic', 'average')

    def test_periodic_fifo_partial(self):
        self._cycle('periodic', 'fifo')

    def test_realtime_average_partial(self):
        self._cycle('real_time', 'average')

    def test_realtime_fifo_partial(self):
        self._cycle('real_time', 'fifo')

    def test_periodic_month_without_bill(self):
        self._cycle('periodic', 'average', 0)

    def test_realtime_full_bill(self):
        self._cycle('real_time', 'average', 10)
