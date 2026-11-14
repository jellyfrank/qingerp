from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from lxml import etree
import logging

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestRepairPricing(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('sales_team.group_sale_manager')
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({'name': 'Repair pricing test', 'code': 'QRP', 'company_id': cls.env.company.id})
        cls.service = cls._create_product(name='Repair labour', type='service', list_price=100, taxes_id=[Command.clear()])
        cls.part = cls._create_product(name='Repair part', type='consu', list_price=50, standard_price=7, taxes_id=[Command.clear()])
        cls.operator = new_test_user(cls.env, login='qing_repair_operator', groups='stock.group_stock_user,base.group_partner_manager', company_id=cls.env.company.id)

    def _repair(self, mode=None, **values):
        vals = {'partner_id': self.partner_a.id, 'picking_type_id': self.warehouse.repair_type_id.id}
        if mode:
            vals['qing_pricing_mode'] = mode
        vals.update(values)
        return self.env['repair.order'].create(vals)

    def _service(self, repair, **values):
        return self.env['repair.service.line'].create({
            'repair_id': repair.id, 'product_id': self.service.id, 'quantity': 2,
            'qing_price_unit': 123, 'qing_tax_ids': [Command.clear()], **values,
        })

    def _finish(self, repair):
        repair.action_validate()
        for move in repair.move_ids:
            move.quantity = move.product_uom_qty
        repair.action_repair_end()
        self.assertEqual(repair.state, 'done')

    def test_default_setting_new_existing_and_copy(self):
        self.env.company.qing_repair_pricing_mode = 'sale'
        repair = self._repair()
        self.assertEqual(repair.qing_pricing_mode, 'sale')
        self.env.company.qing_repair_pricing_mode = 'direct'
        defaults = self.env['repair.order'].default_get(['company_id', 'qing_pricing_mode'])
        self.assertEqual(defaults['qing_pricing_mode'], 'direct')
        self.assertEqual(repair.qing_pricing_mode, 'sale')
        self.assertEqual(self._repair().qing_pricing_mode, 'direct')
        self.assertEqual(repair.copy().qing_pricing_mode, 'direct')

    def test_company_defaults_are_independent(self):
        other = self.env['res.company'].create({'name': 'Other repair company', 'qing_repair_pricing_mode': 'direct'})
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', other.id)], limit=1)
        if not warehouse:
            warehouse = self.env['stock.warehouse'].create({'name': 'Other repair warehouse', 'code': 'QRO', 'company_id': other.id})
        other_repair = self.env['repair.order'].with_company(other).create({'company_id': other.id, 'picking_type_id': warehouse.repair_type_id.id})
        self.assertEqual(other_repair.qing_pricing_mode, 'direct')
        self.assertEqual(self._repair().qing_pricing_mode, 'sale')

    def test_mode_change_requires_admin_and_empty_draft(self):
        repair = self._repair()
        with self.assertRaises(AccessError):
            repair.with_user(self.operator).write({'qing_pricing_mode': 'direct'})
        repair.write({'qing_pricing_mode': 'direct'})
        repair.action_validate()
        with self.assertRaises(UserError):
            repair.write({'qing_pricing_mode': 'sale'})
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.operator).write({'qing_repair_pricing_mode': 'direct'})

    def test_direct_native_invoice_amount_and_repeat(self):
        repair = self._repair('direct')
        service = self._service(repair, qing_discount=10)
        self.assertAlmostEqual(repair.qing_amount_total, 221.4)
        self._finish(repair)
        action = repair.action_create_invoice()
        invoice = self.env['account.move'].browse(action['res_id'])
        self.assertFalse(repair.sale_order_id)
        self.assertEqual(invoice.repair_order_id, repair)
        self.assertEqual(invoice.invoice_line_ids.repair_service_line_id, service)
        self.assertAlmostEqual(invoice.amount_total, repair.qing_amount_total)
        self.assertEqual(repair.action_create_invoice()['res_id'], invoice.id)
        self.assertEqual(len(repair.invoice_ids), 1)
        invoice.action_post()
        with self.assertRaises(UserError):
            service.write({'qing_price_unit': 999})
        with self.assertRaises(UserError):
            repair.action_create_invoice()

    def test_part_charge_does_not_use_stock_cost_and_zero_is_valid(self):
        repair = self._repair('direct')
        move = self.env['stock.move'].create({
            'repair_id': repair.id, 'product_id': self.part.id, 'product_uom_qty': 1,
            'repair_line_type': 'add', 'company_id': repair.company_id.id,
            'qing_price_unit': 0, 'price_unit': 7, 'qing_tax_ids': [Command.clear()],
        })
        self._finish(repair)
        invoice = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        self.assertEqual(invoice.invoice_line_ids.stock_move_id, move)
        self.assertEqual(invoice.invoice_line_ids.price_unit, 0)
        self.assertEqual(move.price_unit, 7)

    def test_quotation_path_is_native_and_excludes_unrelated_lines(self):
        repair = self._repair('sale')
        service = self._service(repair)
        repair.action_create_sale_order()
        order = repair.sale_order_id
        service.sale_line_id.price_unit = 333
        self.env['sale.order.line'].create({'order_id': order.id, 'product_id': self.service.id, 'product_uom_qty': 1, 'price_unit': 999, 'tax_ids': [Command.clear()]})
        self.assertEqual(repair.qing_amount_total, 666)
        self.assertEqual(order.amount_total, 1665)
        with self.assertRaises(UserError):
            repair.action_create_invoice()
        with self.assertRaises(UserError):
            repair.write({'qing_pricing_mode': 'direct'})
        order.action_confirm()
        invoice = order._create_invoices()
        self.assertTrue(invoice)
        self.assertFalse(invoice.repair_order_id)

    def test_sales_origin_forces_quotation_despite_company_default(self):
        self.env.company.qing_repair_pricing_mode = 'direct'
        order = self.env['sale.order'].create({'partner_id': self.partner_a.id})
        repair = self._repair(sale_order_id=order.id)
        self.assertEqual(repair.qing_pricing_mode, 'sale')
        with self.assertRaises(ValidationError):
            self._repair('direct', sale_order_id=order.id)

    def test_warranty_and_global_tax_rounding_match_invoice(self):
        self.env.company.tax_calculation_rounding_method = 'round_globally'
        tax = self.tax_sale_a.copy({'name': 'Repair rounding VAT 13', 'amount': 13, 'amount_type': 'percent', 'price_include_override': 'tax_excluded'})
        repair = self._repair('direct')
        for index in range(3):
            self._service(repair, quantity=1, qing_price_unit=0.03, qing_tax_ids=[Command.set(tax.ids)])
        self._finish(repair)
        invoice = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        self.assertAlmostEqual(invoice.amount_total, repair.qing_amount_total)
        free = self._repair('direct', under_warranty=True)
        self._service(free, qing_tax_ids=[Command.set(tax.ids)])
        self._finish(free)
        free_invoice = self.env['account.move'].browse(free.action_create_invoice()['res_id'])
        self.assertEqual(free.qing_amount_total, 0)
        self.assertEqual(free_invoice.amount_total, 0)

    def test_draft_invoice_price_sync(self):
        repair = self._repair('direct')
        service = self._service(repair)
        self._finish(repair)
        invoice = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        service.write({'qing_price_unit': 55, 'qing_discount': 20})
        self.assertAlmostEqual(invoice.amount_total, 88)
        self.assertAlmostEqual(invoice.amount_total, repair.qing_amount_total)

    def test_direct_cannot_create_quotation(self):
        repair = self._repair('direct')
        self._service(repair)
        with self.assertRaises(UserError):
            repair.action_create_sale_order()
        self.assertFalse(repair.sale_order_id)

    def test_invoice_cannot_post_a_different_price_or_quantity(self):
        repair = self._repair('direct')
        service = self._service(repair)
        self._finish(repair)
        invoice = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        invoice.invoice_line_ids.price_unit = 999
        with self.assertRaises(UserError):
            invoice.action_post()
        invoice.invoice_line_ids.price_unit = service.qing_price_unit
        invoice.invoice_line_ids.quantity = 999
        with self.assertRaises(UserError):
            invoice.action_post()
        invoice.invoice_line_ids.quantity = service.quantity
        invoice.action_post()

    def test_price_included_tax_matches_invoice(self):
        tax = self.tax_sale_a.copy({'name': 'Repair VAT included 13', 'amount': 13, 'price_include_override': 'tax_included'})
        repair = self._repair('direct')
        self._service(repair, qing_price_unit=113, qing_tax_ids=[Command.set(tax.ids)])
        self._finish(repair)
        invoice = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        self.assertAlmostEqual(repair.qing_amount_total, 226)
        self.assertAlmostEqual(invoice.amount_total, repair.qing_amount_total)
        self.assertAlmostEqual(invoice.amount_untaxed, repair.qing_amount_untaxed)

    def _assert_can_post_without_inventory_permissions(self, login, groups):
        accountant = new_test_user(self.env, login=login, groups=groups, company_id=self.env.company.id)
        self.assertFalse(accountant.has_group('stock.group_stock_user'))
        repair = self._repair('direct')
        self._service(repair)
        self._finish(repair)
        invoice = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        invoice.with_user(accountant).action_post()
        self.assertEqual(invoice.state, 'posted')

    def test_accountant_can_post_without_inventory_permissions(self):
        self._assert_can_post_without_inventory_permissions('qing_repair_accountant', 'account.group_account_user')

    def test_invoice_user_can_post_without_inventory_permissions_in_standard_product(self):
        if self.env['ir.module.module'].search_count([('name', '=', 'bgwy_account'), ('state', '=', 'installed')]):
            self.skipTest('Customer invoice-only policy is verified in bgwy_account tests.')
        self._assert_can_post_without_inventory_permissions('qing_repair_invoice_user', 'account.group_account_invoice')

    def test_ordinary_stock_moves_have_no_repair_pricing(self):
        product = self.part.copy({'name': 'Ordinary stock item', 'taxes_id': [Command.set(self.tax_sale_a.ids)]})
        move = self.env['stock.move'].create({
            'product_id': product.id, 'product_uom_qty': 1,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
        })
        self.assertFalse(move.repair_id)
        self.assertEqual(move.qing_price_unit, 0)
        self.assertFalse(move.qing_tax_ids)

    def test_auto_invoice_defaults_off_and_requires_administrator(self):
        self.assertFalse(self.env.company.qing_repair_auto_invoice)
        repair = self._repair('direct')
        self._service(repair)
        self._finish(repair)
        self.assertFalse(repair.invoice_ids)
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.operator).write({'qing_repair_auto_invoice': True})

    def test_auto_invoice_is_draft_and_repeat_completion_is_idempotent(self):
        self.env.company.qing_repair_auto_invoice = True
        repair = self._repair('direct')
        self._service(repair)
        self._finish(repair)
        invoice = repair.invoice_ids
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.state, 'draft')
        self.assertFalse(repair.sale_order_id)
        repair.action_repair_done()
        self.assertEqual(repair.invoice_ids, invoice)
        self.assertEqual(repair.action_create_invoice()['res_id'], invoice.id)

    def test_auto_invoice_preserves_quotation_path_and_zero_charge(self):
        self.env.company.qing_repair_auto_invoice = True
        quotation = self._repair('sale')
        self._service(quotation)
        self._finish(quotation)
        self.assertFalse(quotation.invoice_ids)
        free = self._repair('direct', under_warranty=True)
        self._service(free)
        self._finish(free)
        self.assertEqual(free.invoice_ids.state, 'draft')
        self.assertEqual(free.invoice_ids.amount_total, 0)
        empty = self._repair('direct')
        self._finish(empty)
        self.assertFalse(empty.invoice_ids)

    def test_auto_invoice_does_not_bypass_account_permissions(self):
        self.env.company.qing_repair_auto_invoice = True
        repair = self._repair('direct')
        self._service(repair)
        repair.action_validate()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            repair.with_user(self.operator).action_repair_end()
        self.assertEqual(repair.state, 'confirmed')
        self.assertFalse(repair.invoice_ids)

    def test_auto_invoice_batch_and_consumption_warning(self):
        self.env.company.qing_repair_auto_invoice = True
        repairs = self._repair('direct') | self._repair('direct')
        for repair in repairs:
            self._service(repair)
            repair.action_validate()
        repairs.action_repair_done()
        self.assertEqual(len(repairs.invoice_ids), 2)
        repair = self._repair('direct')
        self._service(repair)
        part = self.env['stock.move'].create({
            'repair_id': repair.id, 'product_id': self.part.id, 'product_uom_qty': 1,
            'repair_line_type': 'add', 'company_id': repair.company_id.id,
        })
        repair.action_validate()
        part.quantity = 0
        warning = repair.action_repair_end()
        self.assertEqual(warning['res_model'], 'repair.consumption.warning')
        self.assertEqual(repair.state, 'confirmed')
        self.assertFalse(repair.invoice_ids)
        repair.action_repair_done()
        self.assertEqual(repair.invoice_ids.state, 'draft')

    def test_billing_states_follow_real_payments_and_exports(self):
        repair = self._repair('direct')
        self._service(repair)
        self.assertEqual(repair.qing_invoice_state, 'none')
        self._finish(repair)
        invoice = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        self.assertEqual(repair.qing_invoice_state, 'draft')
        self.assertEqual(repair.qing_payment_state, 'none')
        invoice.action_post()
        self.assertEqual(repair.qing_invoice_state, 'posted')
        self.assertEqual(repair.qing_payment_state, 'not_paid')
        self._register_payment(invoice, amount=100)
        self.assertEqual(repair.qing_payment_state, 'partial')
        self._register_payment(invoice, amount=invoice.amount_residual)
        self.assertIn(repair.qing_payment_state, ('paid', 'in_payment'))
        self.assertEqual(repair.with_user(self.operator).qing_invoice_state, 'posted')
        fields = ['qing_invoice_state', 'qing_payment_state', 'qing_amount_total']
        self.assertEqual(repair.export_data(fields)['datas'][0][2], 246)

    def test_billing_states_multiple_invoices_cancel_and_refund(self):
        repair = self._repair('direct')
        self._service(repair)
        self._finish(repair)
        first = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        first.action_post()
        self._service(repair, qing_price_unit=55)
        second = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        self.assertEqual(repair.qing_invoice_state, 'mixed')
        second.button_cancel()
        self.assertEqual(repair.qing_invoice_state, 'posted')
        refund = first._reverse_moves([{'date': first.date}], cancel=True)
        self.assertTrue(refund)
        self.assertIn(refund, repair._qing_billing_invoices())
        self.assertIn(repair.qing_payment_state, ('paid', 'reversed', 'mixed'))

    def test_quotation_billing_states_ignore_unrelated_invoice(self):
        repair = self._repair('sale')
        self._service(repair)
        repair.action_create_sale_order()
        unrelated = self.env['sale.order.line'].create({
            'order_id': repair.sale_order_id.id, 'product_id': self.service.id,
            'product_uom_qty': 1, 'price_unit': 999, 'tax_ids': [Command.clear()],
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id,
            'invoice_line_ids': [Command.create({
                'product_id': self.service.id, 'price_unit': 999, 'quantity': 1,
                'sale_line_ids': [Command.set(unrelated.ids)],
            })],
        })
        self.assertIn(invoice, repair.invoice_ids)
        self.assertEqual(repair.qing_invoice_state, 'none')
        self._finish(repair)
        repair.sale_order_id.action_confirm()
        related = repair.sale_order_id._create_invoices()
        self.assertTrue(related & repair._qing_billing_invoices())
        self.assertEqual(repair.qing_invoice_state, 'draft')
        related.action_post()
        self.assertEqual(repair.qing_invoice_state, 'posted')

    def test_new_list_columns_are_optional_in_product(self):
        # The customer layer may show these columns; the product view stays opt-in.
        arch = etree.fromstring(self.env.ref('qing_repair.view_repair_order_list').arch_db)
        names = ['qing_device_model', 'qing_fault_description', 'qing_repair_content',
            'qing_amount_total', 'qing_invoice_state', 'qing_payment_state']
        self.assertEqual([node.get('name') for node in arch.xpath("//field[@optional='hide']")], names)

    def test_mixed_payment_and_all_cancelled_invoices(self):
        repair = self._repair('direct')
        self._service(repair)
        self._finish(repair)
        first = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        first.action_post()
        self._register_payment(first, amount=first.amount_residual)
        self._service(repair, qing_price_unit=50)
        second = self.env['account.move'].browse(repair.action_create_invoice()['res_id'])
        second.action_post()
        self.assertEqual(repair.qing_invoice_state, 'posted')
        self.assertEqual(repair.qing_payment_state, 'partial')
        cancelled = self._repair('direct')
        self._service(cancelled)
        self._finish(cancelled)
        invoice = self.env['account.move'].browse(cancelled.action_create_invoice()['res_id'])
        invoice.button_cancel()
        self.assertEqual(cancelled.qing_invoice_state, 'cancel')
        self.assertEqual(cancelled.qing_payment_state, 'none')

    def test_large_service_documents_keep_amounts_and_one_draft(self):
        self.env.company.qing_repair_auto_invoice = True
        query_counts = []
        for count in (50, 150):
            repair = self._repair('direct', repair_service_line_ids=[Command.create({
                'product_id': self.service.id, 'quantity': 1, 'qing_price_unit': 2,
                'qing_tax_ids': [Command.clear()],
            }) for index in range(count)])
            self.env.flush_all()
            before = self.env.cr.sql_log_count
            repair.repair_service_line_ids[0].qing_price_unit = 3
            self.env.flush_all()
            query_counts.append(self.env.cr.sql_log_count - before)
            _logger.info('Single service-price edit: %s lines, %s SQL queries', count, query_counts[-1])
            self.assertEqual(repair.qing_amount_total, count * 2 + 1)
            self._finish(repair)
            self.assertEqual(len(repair.invoice_ids), 1)
            self.assertEqual(len(repair.invoice_ids.invoice_line_ids), count)
            self.assertEqual(repair.invoice_ids.amount_total, count * 2 + 1)
        self.assertLessEqual(query_counts[1], query_counts[0] + 20)
