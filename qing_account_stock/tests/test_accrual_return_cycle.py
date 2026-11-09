import logging
from unittest import SkipTest

from freezegun import freeze_time
from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestAccrualReturnCycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.env['ir.module.module'].search_count([('name', '=', 'l10n_cn'), ('state', '=', 'installed')]):
            raise SkipTest('CNF fixture requires l10n_cn')
        company = cls.env['res.company'].with_context(chart_template_load=True).create({
            'name': 'CNF partial returns synthetic', 'country_id': cls.env.ref('base.cn').id,
            'currency_id': cls.env.ref('base.CNY').id})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.company = company.with_env(cls.env)
        cls.env['account.chart.template'].try_loading('cn', cls.company, install_demo=False)
        cls.env['stock.warehouse'].create({'name': 'Partial return', 'code': 'PRT', 'company_id': company.id})
        cls.vendor = cls.env['res.partner'].create({'name': 'Partial return supplier'})
        cls.liability = cls.env['account.account'].create({'name': 'Partial return accrual', 'code': '299905',
            'account_type': 'liability_current', 'company_ids': [Command.set(company.ids)]})
        cls.product = cls.env['product.product'].create({'name': 'Partial return goods', 'type': 'consu',
            'is_storable': True, 'standard_price': 100, 'purchase_method': 'receive',
            'company_id': company.id, 'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})

    def _trace(self, po, stage):
        for move in po.order_line.move_ids.filtered(lambda move: move.state == 'done').sorted('id'):
            _logger.info('CNF_VALUE_TRACE stage=%s method=%s move=%s origin=%s qty=%s value=%s total=%s',
                stage, self.company.cost_method, move.id, move.origin_returned_move_id.id,
                move.quantity, move.value, self.product.total_value)

    def _receive(self, picking, qty):
        picking.move_ids.product_uom_qty = qty
        picking.action_confirm()
        picking.action_assign()
        picking.move_ids.quantity = qty
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(picking.state, 'done')

    def _cycle(self, mode, method, price=100, complete=False):
        self.company.write({'inventory_valuation': mode, 'cost_method': method})
        with freeze_time('2026-08-25 12:00:00'):
            po = self.env['purchase.order'].create({'partner_id': self.vendor.id,
                'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 10,
                    'price_unit': 100, 'tax_ids': [Command.clear()]})]})
            po.button_confirm()
            first = po.picking_ids
            self._receive(first, 6)
            second = first.copy()
            self._receive(second, 4)
            self.assertEqual(po.order_line.qty_received, 10)
            po.action_create_invoice()
            bill = po.invoice_ids
            bill.invoice_date = '2026-08-25'
            bill.invoice_line_ids.write({'quantity': 6, 'price_unit': price})
            bill.action_post()
            if price != 100:
                self._trace(po, 'bill_6')
        with freeze_time('2026-08-26 12:00:00'):
            for receipt in (first, first if complete else second):
                returned = receipt._create_return()
                self._receive(returned, 1)
                if price != 100:
                    self._trace(po, 'return_%s' % receipt.id)
                credit = bill._reverse_moves(default_values_list=[{'date': '2026-08-26', 'invoice_date': '2026-08-26'}])
                credit.invoice_line_ids.quantity = 1
                credit.action_post()
                if price != 100:
                    self._trace(po, 'credit_%s' % receipt.id)
            source = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(po.ids)]})
            row = source._rows()[0]
            self.assertEqual((row['received'], row['invoiced'], row['gap'], row['reference']), (8, 4, 4, 400))
        with freeze_time('2026-08-31 12:00:00'):
            batch = self.env['qing.accrual.batch']._from_wizard(source)
            batch.action_lock()
            preview = self.env['qing.accrual.preview'].create({'batch_id': batch.id,
                'liability_account_id': self.liability.id, 'journal_id': self.company.account_stock_journal_id.id})
            preview.action_create_draft()
            batch.action_post()
            if self.company._action_close_stock_valuation(at_date=fields.Date.to_date('2026-08-31')):
                self.company.action_close_stock_valuation(at_date=fields.Date.to_date('2026-08-31'), auto_post=True)
            lines = self.env['account.move.line'].search([('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
            balance = lambda account: sum(lines.filtered(lambda line: line.account_id == account).mapped('balance'))
            stock = balance(self.company.account_stock_valuation_id)
            expense = sum(lines.filtered(lambda line: line.account_id.account_type in ('expense', 'expense_direct_cost')).mapped('balance'))
            self.assertAlmostEqual(balance(self.liability), -400)
            self.assertAlmostEqual(balance(self.vendor.property_account_payable_id), -4 * price)
            self.assertAlmostEqual(stock, self.product.total_value)
            self.assertAlmostEqual(stock + expense, 400 + 4 * price)
            if price == 100:
                self.assertAlmostEqual(stock, 800)
                self.assertAlmostEqual(expense, 0)
            _logger.info('CNF_PARTIAL_RETURN mode=%s method=%s price=%s stock=%s expense=%s accrual=400 payable=%s',
                mode, method, price, stock, expense, 4 * price)
        with freeze_time('2026-09-01 12:00:00'):
            batch.action_reverse()
            self.assertEqual(batch.state, 'reversed')
            self.assertAlmostEqual(sum((batch.draft_move_id | batch.reversal_move_id).line_ids.filtered(
                lambda line: line.account_id == self.liability).mapped('balance')), 0)
            self.assertFalse(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)]))
            self.assertEqual(source._rows()[0]['reference'], 400)

        if complete:
            with freeze_time('2026-09-30 12:00:00'):
                previous = po.invoice_ids
                po.action_create_invoice()
                final_bill = po.invoice_ids - previous
                final_bill.invoice_date = '2026-09-30'
                final_bill.invoice_line_ids.write({'quantity': 4, 'price_unit': price})
                final_bill.action_post()
                self.assertFalse(source._rows())
                self.assertAlmostEqual(self.product.total_value, 8 * price)
                if self.company._action_close_stock_valuation(at_date='2026-09-30'):
                    self.company.action_close_stock_valuation(at_date='2026-09-30', auto_post=True)
                lines = self.env['account.move.line'].search([
                    ('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
                self.assertAlmostEqual(sum(lines.filtered(lambda l: l.account_id == self.company.account_stock_valuation_id).mapped('balance')), 8 * price)
                self.assertAlmostEqual(sum(lines.filtered(lambda l: l.account_id.account_type in ('expense', 'expense_direct_cost')).mapped('balance')), 0)
                # Later billing must not change the pre-billing valuation query.
                historical_moves = first.move_ids.with_context(at_date=fields.Date.to_date('2026-08-31'))
                old = historical_moves._get_value_from_account_move(6)
                self.assertAlmostEqual(old['value'], 440)

    def test_final_billing_average(self):
        self._cycle('real_time', 'average', 110, complete=True)

    def test_final_billing_periodic_average(self):
        self._cycle('periodic', 'average', 110, complete=True)

    def test_final_billing_periodic_fifo(self):
        self._cycle('periodic', 'fifo', 110, complete=True)

    def test_final_billing_fifo(self):
        self._cycle('real_time', 'fifo', 110, complete=True)

    def test_periodic_average(self):
        self._cycle('periodic', 'average')

    def test_periodic_fifo(self):
        self._cycle('periodic', 'fifo')

    def test_realtime_average(self):
        self._cycle('real_time', 'average')

    def test_realtime_fifo(self):
        self._cycle('real_time', 'fifo')

    def test_price_difference_average_audit(self):
        self._cycle('real_time', 'average', 110)

    def test_price_difference_fifo_audit(self):
        self._cycle('real_time', 'fifo', 110)

    def _post_unbilled_batch(self, po):
        source = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(po.ids)]})
        batch = self.env['qing.accrual.batch']._from_wizard(source)
        batch.action_lock()
        wizard = self.env['qing.accrual.preview'].create({'batch_id': batch.id,
            'liability_account_id': self.liability.id, 'journal_id': self.company.account_stock_journal_id.id})
        wizard.action_create_draft()
        batch.action_post()
        return batch

    def _check_unbilled_close(self, date, stock, accrual, payable):
        if self.company._action_close_stock_valuation(at_date=date):
            self.company.action_close_stock_valuation(at_date=date, auto_post=True)
        lines = self.env['account.move.line'].search([('company_id', '=', self.company.id),
            ('parent_state', '=', 'posted'), ('date', '<=', date)])
        balance = lambda account: sum(lines.filtered(lambda line: line.account_id == account).mapped('balance'))
        self.assertAlmostEqual(self.product.total_value, stock)
        self.assertAlmostEqual(balance(self.company.account_stock_valuation_id), stock)
        self.assertAlmostEqual(balance(self.liability), -accrual)
        self.assertAlmostEqual(balance(self.vendor.property_account_payable_id), -payable)
        self.assertAlmostEqual(sum(lines.filtered(lambda line: line.account_id.account_type in ('expense', 'expense_direct_cost')).mapped('balance')), 0)
        _logger.info('CNF_UNBILLED_RETURN mode=%s method=%s date=%s stock=%s accrual=%s payable=%s',
            self.company.inventory_valuation, self.company.cost_method, date, stock, accrual, payable)

    def _unbilled_return_cycle(self, mode, method, price=100, before_accrual=False):
        self.company.write({'inventory_valuation': mode, 'cost_method': method})
        with freeze_time('2026-08-31 12:00:00'):
            po = self.env['purchase.order'].create({'partner_id': self.vendor.id,
                'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 10,
                    'price_unit': 100, 'tax_ids': [Command.clear()]})]})
            po.button_confirm()
            receipt = po.picking_ids
            self._receive(receipt, 10)
            if before_accrual:
                self._receive(receipt._create_return(), 2)
            old = self._post_unbilled_batch(po)
            amount = 800 if before_accrual else 1000
            self._check_unbilled_close('2026-08-31', amount, amount, 0)
        with freeze_time('2026-09-01 12:00:00'):
            if not before_accrual:
                self._receive(receipt._create_return(), 2)
            self.assertFalse(po.invoice_ids)
            self.assertEqual(po.order_line.qty_received, 8)
            old.action_reverse()
            self.assertAlmostEqual(sum(old.reversal_move_id.line_ids.mapped('credit')), -amount)
        with freeze_time('2026-09-30 12:00:00'):
            new = self._post_unbilled_batch(po)
            self._check_unbilled_close('2026-09-30', 800, 800, 0)
        with freeze_time('2026-10-01 12:00:00'):
            new.action_reverse()
        with freeze_time('2026-10-31 12:00:00'):
            po.action_create_invoice()
            bill = po.invoice_ids
            self.assertEqual(bill.invoice_line_ids.quantity, 8)
            bill.invoice_date = '2026-10-31'
            bill.invoice_line_ids.price_unit = price
            bill.action_post()
            self.assertFalse(po.invoice_ids.filtered(lambda move: move.move_type == 'in_refund'))
            source = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(po.ids)]})
            self.assertFalse(source._rows())
            self._check_unbilled_close('2026-10-31', 8 * price, 0, 8 * price)
            historical_moves = receipt.move_ids.with_context(at_date=fields.Date.to_date('2026-09-30'))
            historical = historical_moves._get_value_from_account_move(10)
            self.assertEqual(historical['quantity'], 0)
            self.assertEqual(historical['value'], 0)
            self.assertFalse(self.env['qing.accrual.source'].search([('purchase_line_id', 'in', po.order_line.ids)]))

    def test_unbilled_return_periodic_average(self):
        self._unbilled_return_cycle('periodic', 'average')

    def test_unbilled_return_periodic_fifo(self):
        self._unbilled_return_cycle('periodic', 'fifo')

    def test_unbilled_return_realtime_average(self):
        self._unbilled_return_cycle('real_time', 'average')

    def test_unbilled_return_realtime_fifo(self):
        self._unbilled_return_cycle('real_time', 'fifo')

    def test_unbilled_return_before_accrual(self):
        self._unbilled_return_cycle('real_time', 'average', before_accrual=True)

    def test_unbilled_return_price_change_average(self):
        self._unbilled_return_cycle('real_time', 'average', price=110)

    def test_unbilled_return_price_change_fifo(self):
        self._unbilled_return_cycle('real_time', 'fifo', price=110)

    def test_unbilled_return_price_change_periodic_average(self):
        self._unbilled_return_cycle('periodic', 'average', price=110)

    def test_unbilled_return_price_change_periodic_fifo(self):
        self._unbilled_return_cycle('periodic', 'fifo', price=110)

    def _landed_sale_cycle(self, mode, method, freight_amount):
        for module in ('sale_stock', 'stock_landed_costs'):
            if not self.env['ir.module.module'].search_count([('name', '=', module), ('state', '=', 'installed')]):
                raise SkipTest('Landed/sale fixture requires %s' % module)
        self.company.write({'inventory_valuation': mode, 'cost_method': method})
        with freeze_time('2026-08-31 12:00:00'):
            po = self.env['purchase.order'].create({'partner_id': self.vendor.id,
                'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 10,
                    'price_unit': 100, 'tax_ids': [Command.clear()]})]})
            po.button_confirm()
            receipt = po.picking_ids
            self._receive(receipt, 10)
            batch = self._post_unbilled_batch(po)
            self._check_unbilled_close('2026-08-31', 1000, 1000, 0)
        with freeze_time('2026-09-01 12:00:00'):
            self._receive(receipt._create_return(), 2)
            batch.action_reverse()
        with freeze_time('2026-09-02 12:00:00'):
            customer = self.env['res.partner'].create({'name': 'Landed sale customer'})
            sale = self.env['sale.order'].create({'partner_id': customer.id,
                'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 3,
                    'price_unit': 200, 'tax_ids': [Command.clear()]})]})
            sale.action_confirm()
            self._receive(sale.picking_ids, 3)
            invoice = sale._create_invoices()
            invoice.invoice_date = '2026-09-02'
            invoice.action_post()
            self.assertEqual(invoice.amount_total, 600)
            self.assertEqual(self.product.qty_available, 5)
        with freeze_time('2026-09-03 12:00:00'):
            po.action_create_invoice()
            bill = po.invoice_ids
            bill.invoice_date = '2026-09-03'
            bill.invoice_line_ids.price_unit = 110
            bill.action_post()
            self.assertEqual(bill.amount_total, 880)
        with freeze_time('2026-09-04 12:00:00'):
            if freight_amount:
                freight = self.env['product.product'].create({'name': 'Nonrefundable freight', 'type': 'service',
                    'landed_cost_ok': True, 'supplier_taxes_id': [Command.clear()]})
                freight_bill = self.env['account.move'].create({'move_type': 'in_invoice', 'partner_id': self.vendor.id,
                    'invoice_date': '2026-09-04', 'invoice_line_ids': [Command.create({'product_id': freight.id,
                        'quantity': 1, 'price_unit': freight_amount, 'tax_ids': [Command.clear()]})]})
                freight_bill.action_post()
                cost = self.env['stock.landed.cost'].create({'picking_ids': [Command.set(receipt.ids)],
                    'vendor_bill_id': freight_bill.id, 'account_journal_id': self.company.account_stock_journal_id.id,
                    'cost_lines': [Command.create({'product_id': freight.id, 'name': 'Freight',
                        'price_unit': freight_amount, 'split_method': 'by_quantity',
                        'account_id': freight_bill.invoice_line_ids.account_id.id})]})
                cost.compute_landed_cost()
                self.assertEqual(sum(cost.valuation_adjustment_lines.mapped('additional_landed_cost')), freight_amount)
                cost.button_validate()
                self.assertEqual(cost.state, 'done')
        with freeze_time('2026-09-30 12:00:00'):
            if self.company._action_close_stock_valuation(at_date='2026-09-30'):
                self.company.action_close_stock_valuation(at_date='2026-09-30', auto_post=True)
            lines = self.env['account.move.line'].search([('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
            balance = lambda account: sum(lines.filtered(lambda line: line.account_id == account).mapped('balance'))
            stock = balance(self.company.account_stock_valuation_id)
            expense = sum(lines.filtered(lambda line: line.account_id.account_type in ('expense', 'expense_direct_cost')).mapped('balance'))
            self.assertAlmostEqual(stock, 5 * (110 + freight_amount / 10))
            self.assertAlmostEqual(self.product.total_value, stock)
            self.assertAlmostEqual(expense, 880 + freight_amount - stock)
            self.assertAlmostEqual(balance(self.vendor.property_account_payable_id), -880 - freight_amount)
            self.assertAlmostEqual(balance(customer.property_account_receivable_id), 600)
            self.assertAlmostEqual(balance(self.liability), 0)
            self.assertFalse(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)]))
            _logger.info('CNF_LANDED_SALE mode=%s method=%s freight=%s stock=%s expense=%s payable=%s receivable=600',
                mode, method, freight_amount, stock, expense, 880 + freight_amount)

    def test_sale_periodic_average(self):
        self._landed_sale_cycle('periodic', 'average', 0)

    def test_sale_periodic_fifo(self):
        self._landed_sale_cycle('periodic', 'fifo', 0)

    def test_sale_realtime_average(self):
        self._landed_sale_cycle('real_time', 'average', 0)

    def test_sale_realtime_fifo(self):
        self._landed_sale_cycle('real_time', 'fifo', 0)

    def test_landed_sale_periodic_average(self):
        self._landed_sale_cycle('periodic', 'average', 100)

    def test_landed_sale_periodic_fifo(self):
        self._landed_sale_cycle('periodic', 'fifo', 100)

    def test_landed_sale_realtime_average(self):
        self._landed_sale_cycle('real_time', 'average', 100)

    def test_landed_sale_realtime_fifo(self):
        self._landed_sale_cycle('real_time', 'fifo', 100)

    def _foreign_purchase_cycle(self, mode, method, settlement="full"):
        """Bill-date inventory cost and payment-date exchange loss stay separate."""
        self.company.write({'inventory_valuation': mode, 'cost_method': method})
        usd = self.env.ref('base.USD')
        usd.active = True
        for date, transaction_date, rate in [('2026-08-01', '2026-08-01', 7),
                                              ('2026-08-10', '2026-08-10', 7.2),
                                              ('2026-08-20', '2026-08-20', 7.3),
                                              ('2026-08-25', '2026-08-25', 7.4)]:
            self.env['res.currency.rate'].create({'currency_id': usd.id,
                'company_id': self.company.id, 'name': date, 'rate': 1 / rate})
            self.assertAlmostEqual(usd._convert(100, self.company.currency_id, self.company, transaction_date), 100 * rate)
        bank = self.env['account.journal'].search([
            ('company_id', '=', self.company.id), ('type', '=', 'bank')], limit=1)
        payment_method = bank.outbound_payment_method_line_ids.filtered(lambda line: line.code == 'manual')[:1]
        self.assertTrue(payment_method and bank.default_account_id)
        # Synthetic immediate settlement; bank-statement clearing is a separate suite.
        payment_method.payment_account_id = bank.default_account_id
        with freeze_time('2026-08-01 12:00:00'):
            po = self.env['purchase.order'].create({'partner_id': self.vendor.id, 'currency_id': usd.id,
                'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 10,
                    'price_unit': 100, 'tax_ids': [Command.clear()]})]})
            po.button_confirm()
            if settlement != 'advance':
                self._receive(po.picking_ids, 10)
                self.assertAlmostEqual(self.product.total_value, 7000)
        advance = self.env['account.payment']
        if settlement == 'advance':
            with freeze_time('2026-08-02 12:00:00'):
                advance = self.env['account.payment'].create({
                    'partner_id': self.vendor.id, 'partner_type': 'supplier',
                    'payment_type': 'outbound', 'amount': 400, 'currency_id': usd.id,
                    'date': '2026-08-02', 'journal_id': bank.id,
                    'payment_method_line_id': payment_method.id})
                advance.action_post()
                self.assertEqual(advance.move_id.state, 'posted')
                self.assertAlmostEqual(self.product.total_value, 0)
            with freeze_time('2026-08-03 12:00:00'):
                self._receive(po.picking_ids, 10)
                self.assertAlmostEqual(self.product.total_value, 7000)
        with freeze_time('2026-08-10 12:00:00'):
            po.action_create_invoice()
            bill = po.invoice_ids
            bill.invoice_date = '2026-08-10'
            bill.action_post()
            self.assertAlmostEqual(bill.amount_total, 1000)
            self.assertAlmostEqual(bill.amount_total_signed, -7200)
            self.assertAlmostEqual(self.product.total_value, 7200)
            if advance:
                advance_line = advance.move_id.line_ids.filtered(
                    lambda line: line.account_id == self.vendor.property_account_payable_id)
                bill.js_assign_outstanding_line(advance_line.id)
                self.assertAlmostEqual(bill.amount_residual, 600)
                self.assertTrue(advance_line.reconciled)
                self.assertAlmostEqual(self.product.total_value, 7200)
        payments = [('2026-08-20', 1000)]
        if settlement == 'partial':
            payments = [('2026-08-20', 400), ('2026-08-25', 600)]
        elif settlement == 'advance':
            payments = [('2026-08-20', 600)]
        for payment_date, amount in payments:
            with freeze_time(payment_date + ' 12:00:00'):
                register = self.env['account.payment.register'].with_context(
                    active_model='account.move', active_ids=bill.ids).create({
                        'journal_id': bank.id, 'payment_method_line_id': payment_method.id,
                        'payment_date': payment_date, 'currency_id': usd.id, 'amount': amount})
                register._create_payments()
                expected_residual = 600 if settlement == 'partial' and amount == 400 else 0
                self.assertAlmostEqual(bill.amount_residual, expected_residual)
                self.assertAlmostEqual(self.product.total_value, 7200)
        closing_date = payments[-1][0]
        with freeze_time(closing_date + ' 12:00:00'):
            if self.company._action_close_stock_valuation(at_date=closing_date):
                self.company.action_close_stock_valuation(at_date=closing_date, auto_post=True)
            lines = self.env['account.move.line'].search([
                ('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
            balance = lambda account: sum(lines.filtered(lambda line: line.account_id == account).mapped('balance'))
            self.assertAlmostEqual(balance(self.vendor.property_account_payable_id), 0)
            expected_bank, expected_loss, expected_gain = {
                'full': (7300, 100, 0), 'partial': (7360, 160, 0),
                'advance': (7180, 60, -80)}[settlement]
            self.assertAlmostEqual(balance(bank.default_account_id), -expected_bank)
            self.assertAlmostEqual(balance(self.company.account_stock_valuation_id), 7200)
            self.assertAlmostEqual(balance(self.company.expense_currency_exchange_account_id), expected_loss)
            self.assertAlmostEqual(balance(self.company.income_currency_exchange_account_id), expected_gain)
            _logger.info('CNF_FX mode=%s method=%s settlement=%s stock=7200 bank=%s loss=%s gain=%s payable=0',
                mode, method, settlement, expected_bank, expected_loss, expected_gain)
        return bill, bank, advance

    def test_foreign_purchase_periodic_average(self):
        self._foreign_purchase_cycle('periodic', 'average')

    def test_foreign_purchase_periodic_fifo(self):
        self._foreign_purchase_cycle('periodic', 'fifo')

    def test_foreign_purchase_realtime_average(self):
        self._foreign_purchase_cycle('real_time', 'average')

    def test_foreign_purchase_realtime_fifo(self):
        self._foreign_purchase_cycle('real_time', 'fifo')

    def test_foreign_partial_periodic_average(self):
        self._foreign_purchase_cycle('periodic', 'average', 'partial')

    def test_foreign_partial_periodic_fifo(self):
        self._foreign_purchase_cycle('periodic', 'fifo', 'partial')

    def test_foreign_partial_real_time_average(self):
        self._foreign_purchase_cycle('real_time', 'average', 'partial')

    def test_foreign_partial_real_time_fifo(self):
        self._foreign_purchase_cycle('real_time', 'fifo', 'partial')

    def test_foreign_advance_periodic_average(self):
        self._foreign_purchase_cycle('periodic', 'average', 'advance')

    def test_foreign_advance_periodic_fifo(self):
        self._foreign_purchase_cycle('periodic', 'fifo', 'advance')

    def test_foreign_advance_real_time_average(self):
        self._foreign_purchase_cycle('real_time', 'average', 'advance')

    def test_foreign_advance_real_time_fifo(self):
        self._foreign_purchase_cycle('real_time', 'fifo', 'advance')

    def _foreign_unmatch_cycle(self, mode, method):
        bill, bank, advance = self._foreign_purchase_cycle(mode, method, 'advance')
        payable = self.vendor.property_account_payable_id
        payments = self.env['account.payment'].search([
            ('company_id', '=', self.company.id), ('partner_id', '=', self.vendor.id)])
        self.assertEqual(len(payments), 2)
        payment_lines = payments.move_id.line_ids.filtered(lambda line: line.account_id == payable)
        bill_lines = bill.line_ids.filtered(lambda line: line.account_id == payable)
        with freeze_time('2026-08-25 12:00:00'):
            bill_lines.remove_move_reconcile()
            self.assertAlmostEqual(bill.amount_residual, 1000)
            self.assertAlmostEqual(sum(payment_lines.mapped('amount_residual_currency')), 1000)
            self.assertFalse(any(payment_lines.mapped('reconciled')))
            # Odoo 20 removes the previous exchange-difference entries while
            # undoing the partial reconciliations. The remaining company-
            # currency amount therefore stays on payable until it is matched
            # again, while the payment exchange line keeps the journal entry
            # balanced.
            self._assert_foreign_balances(bank, -7180, 40, -60, 7200)
            for payment in (advance, payments - advance):
                line = payment.move_id.line_ids.filtered(lambda line: line.account_id == payable)
                bill.js_assign_outstanding_line(line.id)
                self.assertAlmostEqual(bill.amount_residual, 600 if payment == advance else 0)
            self.assertTrue(all(payment_lines.mapped('reconciled')))
            self.assertTrue(all(bill_lines.mapped('reconciled')))
            self.assertEqual(self.env['account.payment'].search([
                ('company_id', '=', self.company.id), ('partner_id', '=', self.vendor.id)]), payments)
            # The rematch is dated on August 25 in Odoo 20. Its replacement
            # exchange entry clears the foreign amount while retaining the
            # company-currency difference on payable.
            self._assert_foreign_balances(bank, -7180, 60, -80, 7200)

    def _assert_foreign_balances(self, bank, bank_balance, payable_balance, exchange_balance, stock_value):
        lines = self.env['account.move.line'].search([
            ('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
        def balance(accounts):
            return sum(lines.filtered(lambda line: line.account_id in accounts).mapped('balance'))
        self.assertAlmostEqual(balance(bank.default_account_id), bank_balance)
        self.assertAlmostEqual(balance(self.vendor.property_account_payable_id), payable_balance)
        self.assertAlmostEqual(balance(self.company.expense_currency_exchange_account_id |
                                       self.company.income_currency_exchange_account_id), exchange_balance)
        self.assertAlmostEqual(self.product.total_value, stock_value)
        self.assertAlmostEqual(balance(self.company.account_stock_valuation_id), stock_value)

    def test_foreign_unmatch_periodic_average(self):
        self._foreign_unmatch_cycle('periodic', 'average')

    def test_foreign_unmatch_periodic_fifo(self):
        self._foreign_unmatch_cycle('periodic', 'fifo')

    def test_foreign_unmatch_realtime_average(self):
        self._foreign_unmatch_cycle('real_time', 'average')

    def test_foreign_unmatch_realtime_fifo(self):
        self._foreign_unmatch_cycle('real_time', 'fifo')

    def _foreign_unused_refund(self, refund_rate):
        usd = self.env.ref('base.USD')
        usd.active = True
        self.env['res.currency.rate'].create([
            {'currency_id': usd.id, 'company_id': self.company.id, 'name': date, 'rate': 1 / rate}
            for date, rate in [('2026-08-01', 7), ('2026-08-20', refund_rate)]])
        bank = self.env['account.journal'].search([
            ('company_id', '=', self.company.id), ('type', '=', 'bank')], limit=1)
        payments = self.env['account.payment']
        for direction, date in [('outbound', '2026-08-01'), ('inbound', '2026-08-20')]:
            methods = bank.outbound_payment_method_line_ids if direction == 'outbound' else bank.inbound_payment_method_line_ids
            method = methods.filtered(lambda line: line.code == 'manual')[:1]
            self.assertTrue(method and bank.default_account_id)
            method.payment_account_id = bank.default_account_id
            with freeze_time(date + ' 12:00:00'):
                payment = self.env['account.payment'].create({
                    'partner_id': self.vendor.id, 'partner_type': 'supplier', 'payment_type': direction,
                    'amount': 400, 'currency_id': usd.id, 'date': date,
                    'journal_id': bank.id, 'payment_method_line_id': method.id})
                payment.action_post()
                payments |= payment
        lines = payments.move_id.line_ids.filtered(
            lambda line: line.account_id == self.vendor.property_account_payable_id)
        difference = round(400 * (refund_rate - 7), 2)
        with freeze_time('2026-08-20 12:00:00'):
            lines.reconcile()
            self.assertTrue(all(lines.mapped('reconciled')))
            self._assert_foreign_balances(bank, difference, 0, -difference, 0)
            lines.remove_move_reconcile()
            self.assertEqual(sorted(lines.mapped('amount_residual_currency')), [-400, 400])
            self._assert_foreign_balances(bank, difference, -difference, 0, 0)
            lines.reconcile()
            self.assertTrue(all(lines.mapped('reconciled')))
            self._assert_foreign_balances(bank, difference, 0, -difference, 0)
            self.assertEqual(self.env['account.payment'].search_count([
                ('company_id', '=', self.company.id), ('partner_id', '=', self.vendor.id)]), 2)

    def test_foreign_unused_refund_gain(self):
        self._foreign_unused_refund(7.3)

    def test_foreign_unused_refund_loss(self):
        self._foreign_unused_refund(6.8)

    def test_foreign_historical_statement_after_unmatch(self):
        bill, bank, advance = self._foreign_purchase_cycle('real_time', 'average', 'advance')
        payments = self.env['account.payment'].search([
            ('company_id', '=', self.company.id), ('partner_id', '=', self.vendor.id)])
        payable = self.vendor.property_account_payable_id
        def snapshot(stage):
            result = {}
            for cutoff in ('2026-08-05', '2026-08-15', '2026-08-21'):
                wizard = self.env['qing.partner.statement'].create({
                    'date_from': '2026-08-01', 'date_to': cutoff,
                    'account_type': 'liability_payable', 'partner_ids': [Command.set(self.vendor.ids)]})
                groups = wizard._statement_data()['groups']
                result[cutoff] = [{key: group[key] for key in
                    ('closing', 'residual', 'residual_currency', 'aging')} for group in groups]
            _logger.info('CNF_FX_HISTORY stage=%s snapshots=%s', stage, result)
            return result
        frozen_wizard = self.env['qing.partner.statement'].create({
            'date_from': '2026-08-01', 'date_to': '2026-08-21',
            'account_type': 'liability_payable', 'partner_ids': [Command.set(self.vendor.ids)]})
        saved = self.env['qing.statement.snapshot'].browse(frozen_wizard.action_save_snapshot()['res_id'])
        saved_values = saved.read(['result', 'result_file'])[0]
        original = snapshot('paid')
        with freeze_time('2026-08-25 12:00:00'):
            bill.line_ids.filtered(lambda line: line.account_id == payable).remove_move_reconcile()
            undone = snapshot('unmatched')
            for payment in (advance, payments - advance):
                bill.js_assign_outstanding_line(payment.move_id.line_ids.filtered(
                    lambda line: line.account_id == payable).id)
            restored = snapshot('rematched')
            saved.invalidate_recordset()
            self.assertEqual(saved.read(['result', 'result_file'])[0], saved_values)
            fresh = saved.browse(frozen_wizard.action_save_snapshot()['res_id'])
            self.assertNotEqual(saved.result_file, fresh.result_file)
        expected_by_state = {
            'paid': [('2026-08-05', 2800), ('2026-08-15', -4320), ('2026-08-21', 0)],
            'unmatched': [('2026-08-05', 2800), ('2026-08-15', -4400), ('2026-08-21', 40)],
            'rematched': [('2026-08-05', 2800), ('2026-08-15', -4400), ('2026-08-21', 40)],
        }
        for stage, state in [('paid', original), ('unmatched', undone), ('rematched', restored)]:
            for cutoff, expected in expected_by_state[stage]:
                self.assertAlmostEqual(sum(row['closing'] for row in state[cutoff]), expected)
                self.assertAlmostEqual(sum(row['residual'] for row in state[cutoff]), expected)
                self.assertAlmostEqual(sum(sum(row['aging']) for row in state[cutoff]), -expected)
                expected_fx = {'2026-08-05': 400, '2026-08-15': -600, '2026-08-21': 0}[cutoff]
                self.assertAlmostEqual(sum(row['residual_currency'] for row in state[cutoff]), expected_fx)
        # Audit the current-relationship boundary; this is not frozen historical aging.
        self.assertNotEqual(original['2026-08-15'][0]['aging'], undone['2026-08-15'][0]['aging'])
        self.assertNotEqual(original['2026-08-21'][0]['aging'], restored['2026-08-21'][0]['aging'])
