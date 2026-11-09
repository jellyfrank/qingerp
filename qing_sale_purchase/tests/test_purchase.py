import logging
import time

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, tagged, new_test_user

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestManualPurchase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer, cls.vendor, cls.vendor2 = cls.env['res.partner'].create([
            {'name': 'Manual purchase customer'}, {'name': 'Vendor A'}, {'name': 'Vendor B'},
        ])
        cls.product = cls.env['product.product'].create({
            'name': 'Trading goods', 'type': 'consu', 'purchase_ok': True,
            'list_price': 20, 'standard_price': 7,
        })
        cls.buyer = new_test_user(cls.env, login='qing_buyer', groups='purchase.group_purchase_user,sales_team.group_sale_salesman')
        cls.seller = new_test_user(cls.env, login='qing_seller', groups='sales_team.group_sale_salesman_all_leads')
        cls.sales_team = cls.env['crm.team'].create({
            'name': 'Manual purchase sales team',
            'member_ids': [Command.set((cls.buyer | cls.seller).ids)],
        })

    def _order(self, count=1, quantity=100):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id, 'user_id': self.buyer.id,
            'team_id': self.sales_team.id,
            'order_line': [Command.create({
                'product_id': self.product.id, 'product_uom_qty': quantity,
            }) for _ in range(count)],
        })
        order.action_confirm()
        return order

    def _wizard(self, order, quantity=None, vendor=None):
        action = order.action_qing_create_purchase()
        wizard = self.env['qing.sale.purchase.wizard'].browse(action['res_id'])
        wizard.user_id = self.buyer
        wizard.line_ids.write({
            'supplier_id': (vendor or self.vendor).id, 'price_unit': 8,
        })
        if quantity is not None:
            wizard.line_ids.quantity = quantity
        return wizard

    def test_partial_split_idempotent(self):
        order = self._order()
        wizard = self._wizard(order, 60)
        wizard.action_create_purchase()
        first = wizard.purchase_order_ids
        self.assertEqual(first.state, 'draft')
        self.assertEqual(first.order_line.sale_line_id, order.order_line)
        self.assertEqual(order.order_line.qing_purchase_remaining_qty, 40)
        wizard.action_create_purchase()
        self.assertEqual(wizard.purchase_order_ids, first)
        second = self._wizard(order, vendor=self.vendor2)
        second.action_create_purchase()
        self.assertEqual(second.purchase_order_ids.order_line.product_qty, 40)
        self.assertEqual(order.order_line.qing_purchase_remaining_qty, 0)
        self.assertEqual(order.purchase_order_count, 2)

    def test_two_vendors_same_product_no_line_merge(self):
        order = self._order(3)
        wizard = self._wizard(order)
        wizard.line_ids[2].supplier_id = self.vendor2
        wizard.action_create_purchase()
        self.assertEqual(len(wizard.purchase_order_ids), 2)
        self.assertEqual(len(wizard.purchase_order_ids.order_line), 3)

    def test_stale_wizard_and_direct_po_increase_blocked(self):
        order = self._order()
        first, second = self._wizard(order, 70), self._wizard(order, 70)
        first.action_create_purchase()
        with self.assertRaises(UserError), self.cr.savepoint():
            second.action_create_purchase()
        with self.assertRaises(UserError), self.cr.savepoint():
            first.purchase_order_ids.order_line.product_qty = 101
        self.assertEqual(order.order_line.qing_purchase_qty, 70)

    def test_cancel_delete_reopen_and_copy(self):
        order = self._order()
        wizard = self._wizard(order)
        wizard.action_create_purchase()
        purchase = wizard.purchase_order_ids
        self.assertFalse(purchase.copy().order_line.sale_line_id)
        self.assertFalse(order.copy().order_line.purchase_line_ids)
        purchase.button_cancel()
        self.assertEqual(order.order_line.qing_purchase_remaining_qty, 100)
        replacement = self._wizard(order)
        replacement.action_create_purchase()
        with self.assertRaises(UserError), self.cr.savepoint():
            purchase.button_draft()
        replacement.purchase_order_ids.order_line.unlink()
        self.assertEqual(order.order_line.qing_purchase_remaining_qty, 100)

    def test_order_quantity_change_and_cancel_activity(self):
        order = self._order()
        wizard = self._wizard(order, 60)
        wizard.action_create_purchase()
        purchase = wizard.purchase_order_ids
        order.order_line.product_uom_qty = 120
        self.assertEqual(order.order_line.qing_purchase_remaining_qty, 60)
        order.order_line.product_uom_qty = 40
        self.assertEqual(order.order_line.qing_purchase_excess_qty, 20)
        self.assertEqual(purchase.order_line.product_qty, 60)
        self.assertTrue(purchase.activity_ids)
        order._action_cancel()
        self.assertEqual(purchase.state, 'draft')

    def test_permissions_and_manager_overage(self):
        order = self._order()
        with self.assertRaises(AccessError):
            order.with_user(self.seller).action_qing_create_purchase()
        wizard = self._wizard(order, 110)
        wizard.line_ids.overage_reason = 'Minimum order quantity'
        wizard.action_create_purchase()
        self.assertEqual(order.order_line.qing_purchase_excess_qty, 10)
        order2 = self._order()
        action = order2.with_user(self.buyer).action_qing_create_purchase()
        regular = self.env['qing.sale.purchase.wizard'].with_user(self.buyer).browse(action['res_id'])
        regular.line_ids.write({'supplier_id': self.vendor.id, 'quantity': 110,
                               'price_unit': 8, 'overage_reason': 'Forged reason'})
        with self.assertRaises(UserError), self.cr.savepoint():
            regular.action_create_purchase()
        regular.line_ids.quantity = 50
        regular.action_create_purchase()
        self.assertEqual(regular.purchase_order_ids.create_uid, self.buyer)
        summary = order2.with_user(self.seller).order_line.qing_purchase_qty
        self.assertEqual(summary, 50)
        with self.assertRaises(AccessError):
            regular.purchase_order_ids.with_user(self.seller).read(['amount_total'])

    def test_price_and_tampered_source(self):
        order = self._order()
        wizard = self._wizard(order)
        wizard.line_ids.price_unit = -1
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard.action_create_purchase()
        wizard.line_ids.write({'price_unit': 0})
        wizard.action_create_purchase()
        self.assertEqual(wizard.purchase_order_ids.order_line.price_unit, 0)
        other = self._order()
        forged = self._wizard(other)
        forged.line_ids.sale_line_id = order.order_line
        with self.assertRaises(UserError), self.cr.savepoint():
            forged.action_create_purchase()

    def test_form_vendor_pricing(self):
        self.env['product.supplierinfo'].create({
            'partner_id': self.vendor.id, 'product_id': self.product.id,
            'price': 9, 'min_qty': 1, 'discount': 10,
        })
        wizard = self._wizard(self._order())
        with Form(wizard) as form:
            form.supplier_id = self.vendor
        self.assertEqual(wizard.line_ids.price_unit, 9)
        self.assertEqual(wizard.line_ids.discount, 10)
        wizard.action_create_purchase()
        self.assertEqual(wizard.purchase_order_ids.order_line.price_unit, 9)
        self.assertEqual(wizard.purchase_order_ids.order_line.discount, 10)

    def test_performance_50_150(self):
        samples = []
        for count in (50, 150):
            with self.cr.savepoint():
                order = self._order(count)
                self.env.flush_all()
                before = self.cr.sql_log_count
                start = time.monotonic()
                order.order_line[0].product_uom_qty = 110
                self.env.flush_all()
                edit_queries = self.cr.sql_log_count - before
                wizard = self._wizard(order)
                before = self.cr.sql_log_count
                wizard.action_create_purchase()
                self.env.flush_all()
                queries = self.cr.sql_log_count - before
                samples.append((count, edit_queries, queries))
                _logger.info('QING_PURCHASE_PERF lines=%s edit_sql=%s create_sql=%s elapsed=%.3f',
                             count, edit_queries, queries, time.monotonic() - start)
                self.assertEqual(len(wizard.purchase_order_ids.order_line), count)
                self.assertEqual(order.order_line.mapped('qing_purchase_remaining_qty'), [0.0] * count)
        self.assertLess(samples[1][1], samples[0][1] * 2 + 20)
        self.assertLess(samples[1][2], samples[0][2] * 4)

    def test_uom_conversion(self):
        self.env.user.group_ids += self.env.ref('uom.group_uom')
        box = self.env['uom.uom'].create({
            'name': 'Box of ten', 'relative_uom_id': self.product.uom_id.id,
            'relative_factor': 10,
        })
        self.product.extra_uom_ids = [Command.link(box.id)]
        order = self._order()
        wizard = self._wizard(order, 6)
        wizard.line_ids.uom_id = box
        wizard.action_create_purchase()
        self.assertEqual(order.order_line.qing_purchase_qty, 60)
        self.assertEqual(order.order_line.qing_purchase_remaining_qty, 40)
        self.assertEqual(wizard.purchase_order_ids.order_line.uom_id, box)

    def test_company_and_product_changes(self):
        order = self._order()
        wizard = self._wizard(order)
        other = self.env['res.company'].create({'name': 'Other purchase company'})
        foreign_vendor = self.env['res.partner'].create({'name': 'Foreign vendor', 'company_id': other.id})
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard.line_ids.supplier_id = foreign_vendor
            wizard.action_create_purchase()
        wizard.action_create_purchase()
        product = self.product.copy()
        with self.assertRaises(UserError), self.cr.savepoint():
            order.order_line.product_id = product
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard.purchase_order_ids.order_line.product_id = product

    def test_stock_automatic_route_and_normal_receipt(self):
        if 'move_ids' not in self.env['sale.order.line']._fields:
            self.skipTest('Stock integration is tested in the stock installation pass.')
        order = self._order()
        wizard = self._wizard(order)
        wizard.action_create_purchase()
        purchase = wizard.purchase_order_ids
        purchase.button_confirm()
        self.assertEqual(purchase.state, 'purchase')
        self.assertTrue(purchase.picking_ids)
        self.assertTrue(all(picking.location_dest_id.usage == 'internal' for picking in purchase.picking_ids))
        purchase.picking_ids.move_ids.write({'quantity': 100, 'picked': True})
        purchase.picking_ids.button_validate()
        self.assertEqual(purchase.order_line.qty_received, 100)
        self.assertEqual(order.order_line.qty_delivered, 0)
        self.assertEqual(order.order_line.qing_purchase_qty, 100)
        mto = self.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
        if not mto:
            self.fail('MTO route must exist in the stock integration pass.')
        mto.active = True
        # Change the route after confirmation to test submission-time detection,
        # without relying on the automatic procurement to supply a vendor.
        other = self._order()
        other.order_line.route_ids = mto
        blocked = self._wizard(other)
        with self.assertRaises(UserError), self.cr.savepoint():
            blocked.action_create_purchase()

    def test_vendor_currency_and_purchase_taxes(self):
        self.env.company.country_id = self.env.ref('base.us')
        currency = self.env['res.currency'].create({
            'name': 'QPU', 'symbol': 'Q', 'rounding': 0.01,
            'rate_ids': [Command.create({'name': '2026-01-01', 'rate': 2,
                                         'company_id': self.env.company.id})],
        })
        self.vendor.property_purchase_currency_id = currency
        group = self.env['account.tax.group'].create({'name': 'Purchase tax group'})
        tax = self.env['account.tax'].create({
            'name': 'Purchase 10%', 'amount': 10, 'type_tax_use': 'purchase',
            'tax_group_id': group.id,
        })
        self.product.supplier_taxes_id = tax
        self.env['product.supplierinfo'].create({
            'partner_id': self.vendor.id, 'product_id': self.product.id,
            'currency_id': currency.id, 'price': 12,
        })
        wizard = self._wizard(self._order())
        wizard.line_ids._onchange_price_inputs()
        self.assertEqual(wizard.line_ids.currency_id, currency)
        self.assertEqual(wizard.line_ids.price_unit, 12)
        wizard.action_create_purchase()
        purchase = wizard.purchase_order_ids
        self.assertEqual(purchase.currency_id, currency)
        self.assertEqual(purchase.order_line.tax_ids, tax)
        self.assertEqual(purchase.order_line.price_unit, 12)

    def test_native_service_purchase_only_user(self):
        purchase_only = new_test_user(self.env, login='qing_purchase_only', groups='purchase.group_purchase_user')
        service = self.env['product.product'].create({
            'name': 'Native outsourced service', 'type': 'service',
            'purchase_ok': True, 'service_tracking': 'subcontract',
            'seller_ids': [Command.create({'partner_id': self.vendor.id, 'price': 15})],
        })
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [Command.create({'product_id': service.id, 'product_uom_qty': 3})],
        })
        order.action_confirm()
        purchase = order.order_line.purchase_line_ids.order_id
        self.assertEqual(len(purchase), 1)
        self.assertFalse(purchase.order_line.qing_manual_purchase)
        # The administrator assigns the native RFQ before an ordinary buyer handles it.
        purchase.user_id = purchase_only
        purchase.with_user(purchase_only).button_confirm()
        self.assertEqual(purchase.state, 'purchase')
