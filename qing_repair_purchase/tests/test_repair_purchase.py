import logging
from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestRepairPurchase(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.buyer = new_test_user(cls.env, login='repair_purchase_buyer',
            groups='stock.group_stock_user,purchase.group_purchase_user,base.group_partner_manager')
        cls.part = cls._create_product(name='Repair purchase part', type='consu', purchase_ok=True, standard_price=7)
        cls.service = cls._create_product(name='Repair outsource service', type='service', purchase_ok=True, standard_price=15)
        cls.vendor = cls.env['res.partner'].create({'name': 'Repair purchase vendor', 'supplier_rank': 1})

    def _repair(self, mode='direct'):
        repair = self.env['repair.order'].create({'partner_id': self.partner_a.id, 'qing_pricing_mode': mode})
        self.env['stock.move'].create({'repair_id': repair.id, 'product_id': self.part.id,
            'product_uom_qty': 4, 'repair_line_type': 'add', 'company_id': repair.company_id.id})
        self.env['repair.service.line'].create({'repair_id': repair.id, 'product_id': self.service.id, 'quantity': 2})
        return repair

    def _wizard(self, repair):
        action = repair.with_user(self.buyer).action_qing_create_purchase()
        wizard = self.env['qing.repair.purchase.wizard'].with_user(self.buyer).browse(action['res_id'])
        wizard.supplier_id = self.vendor
        wizard._onchange_vendor()
        return wizard

    def test_both_pricing_modes_traceability_and_repeat(self):
        for mode in ('direct', 'sale'):
            repair = self._repair(mode)
            wizard = self._wizard(repair)
            action = wizard.action_create_purchase()
            order = wizard.purchase_order_id
            self.assertEqual(order.qing_repair_id, repair)
            self.assertEqual(order.origin, repair.name)
            self.assertEqual(len(order.order_line), 2)
            self.assertEqual(order.order_line.qing_repair_move_id, repair.move_ids)
            self.assertEqual(order.order_line.move_dest_ids, repair.move_ids)
            self.assertEqual(repair.purchase_count, 1)
            self.assertEqual(order.repair_count, 1)
            self.assertEqual(order.order_line.qing_repair_service_line_id, repair.repair_service_line_ids)
            self.assertEqual(wizard.action_create_purchase()['res_id'], action['res_id'])
            with self.assertRaises(UserError):
                repair.with_user(self.buyer).action_qing_create_purchase()
            self.assertFalse(repair.invoice_ids)
            self.assertFalse(repair.sale_order_id)

    def test_partial_stale_wizard_and_cancel_release_quantity(self):
        repair = self._repair()
        first = self._wizard(repair)
        stale = self._wizard(repair)
        first.line_ids.filtered('repair_move_id').quantity = 1
        first.line_ids.filtered('repair_service_line_id').selected = False
        first.action_create_purchase()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            stale.action_create_purchase()
        next_wizard = self._wizard(repair)
        self.assertEqual(next_wizard.line_ids.filtered('repair_move_id').quantity, 3)
        first.purchase_order_id.button_cancel()
        self.assertEqual(self._wizard(repair).line_ids.filtered('repair_move_id').quantity, 4)

    def test_access_source_and_quantity_validation(self):
        repair = self._repair()
        user = new_test_user(self.env, login='repair_no_purchase', groups='stock.group_stock_user')
        with self.assertRaises(AccessError):
            repair.with_user(user).action_qing_create_purchase()
        wizard = self._wizard(repair)
        line = wizard.line_ids.filtered('repair_move_id')
        for quantity in (0, -1, 5):
            line.quantity = quantity
            with self.assertRaises(UserError), self.env.cr.savepoint():
                wizard.action_create_purchase()
        line.quantity = 4
        with self.assertRaises(OverflowError):
            line.quantity = float('inf')
        other = self._repair()
        line.repair_move_id = other.move_ids
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_create_purchase()

    def test_cancelled_repair_and_other_company_vendor(self):
        repair = self._repair()
        wizard = self._wizard(repair)
        company = self.env['res.company'].create({'name': 'Other repair purchasing company'})
        vendor = self.env['res.partner'].create({'name': 'Other company vendor', 'company_id': company.id})
        with self.assertRaises((UserError, AccessError)), self.env.cr.savepoint():
            wizard.supplier_id = vendor
            wizard.action_create_purchase()
        repair.action_repair_cancel()
        with self.assertRaises(UserError):
            wizard.action_create_purchase()

    def test_large_repair_wizard_scales_without_rewriting_source(self):
        counts = []
        for size in (50, 150):
            with self.env.cr.savepoint():
                repair = self.env['repair.order'].create({'partner_id': self.partner_a.id, 'qing_pricing_mode': 'direct'})
                lines = self.env['repair.service.line'].create([{
                    'repair_id': repair.id, 'product_id': self.service.id, 'quantity': 1,
                } for _ in range(size)])
                before_dates = lines.mapped('write_date')
                before = self.env.cr.sql_log_count
                wizard = self._wizard(repair)
                wizard.action_create_purchase()
                count = self.env.cr.sql_log_count - before
                counts.append(count)
                self.assertEqual(len(wizard.purchase_order_id.order_line), size)
                self.assertEqual(lines.mapped('write_date'), before_dates)
                _logger.info('2447 repair purchase: %s lines, %s queries', size, count)
        self.assertLessEqual(counts[1], counts[0] * 3.5 + 50)

    def test_vendor_currency_tax_and_purchase_unit(self):
        currency = self.env['res.currency'].create({'name': 'QRU', 'symbol': 'Q',
            'rounding': 0.01, 'rate_ids': [Command.create({'name': '2026-01-01',
                'rate': 2, 'company_id': self.env.company.id})]})
        self.vendor.property_purchase_currency_id = currency
        tax = self.env['account.tax'].create({'name': 'Repair purchase 10%',
            'amount': 10, 'type_tax_use': 'purchase',
            'tax_group_id': self.env['account.tax.group'].create({'name': 'Repair taxes'}).id})
        self.part.supplier_taxes_id = tax
        self.env['product.supplierinfo'].create({'partner_id': self.vendor.id,
            'product_id': self.part.id, 'currency_id': currency.id, 'price': 12})
        box = self.env['uom.uom'].create({'name': 'Repair box of two',
            'relative_uom_id': self.part.uom_id.id, 'relative_factor': 2})
        self.part.extra_uom_ids = [Command.link(box.id)]
        repair = self._repair()
        wizard = self._wizard(repair)
        self.assertEqual(wizard.currency_id, currency)
        part_line = wizard.line_ids.filtered('repair_move_id')
        self.assertEqual(part_line.price_unit, 12)
        part_line.write({'uom_id': box.id, 'quantity': 2})
        part_line._onchange_price()
        wizard.action_create_purchase()
        purchase = wizard.purchase_order_id
        line = purchase.order_line.filtered('qing_repair_move_id')
        self.assertEqual(purchase.currency_id, currency)
        self.assertEqual(line.tax_ids, tax)
        self.assertEqual(line.uom_id, box)
        self.assertEqual(line.price_unit, 24)
        self.assertEqual(repair._qing_arranged_purchase()[('stock.move', repair.move_ids.id)], 4)

    def test_native_receipt_tracks_repair_without_financial_side_effects(self):
        repair = self._repair()
        wizard = self._wizard(repair)
        wizard.action_create_purchase()
        purchase = wizard.purchase_order_id
        purchase.with_user(self.buyer).button_confirm()
        self.assertEqual(purchase.state, 'purchase')
        self.assertTrue(purchase.picking_ids)
        self.assertEqual(purchase.order_line.filtered('qing_repair_move_id').move_ids.move_dest_ids, repair.move_ids)
        for picking in purchase.picking_ids:
            picking.move_ids.write({'quantity': 4, 'picked': True})
            picking.button_validate()
        self.assertEqual(purchase.order_line.filtered('qing_repair_move_id').qty_received, 4)
        self.assertFalse(repair.sale_order_id)
        self.assertFalse(repair.invoice_ids)
