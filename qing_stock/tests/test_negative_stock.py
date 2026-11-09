from odoo.addons.stock.tests.common import TestStockCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestNegativeStock(TestStockCommon):
    def _make_picking(self, source, destination, picking_type, quantity):
        return self.env['stock.picking'].with_user(self.user_stock_manager).create({
            'picking_type_id': picking_type.id,
            'location_id': source.id,
            'location_dest_id': destination.id,
            'move_ids': [(0, 0, {
                'product_id': self.productA.id,
                'product_uom_qty': quantity,
                'uom_id': self.productA.uom_id.id,
                'location_id': source.id,
                'location_dest_id': destination.id,
            })],
        })

    def _validate(self, picking, user):
        picking.action_confirm()
        picking.move_ids.quantity = picking.move_ids.product_uom_qty
        picking.move_ids.picked = True
        return picking.with_user(user).button_validate()

    def test_internal_transfer_blocks_shortage_and_keeps_quants(self):
        self.env['stock.quant']._update_available_quantity(self.productA, self.stock_location, 2)
        picking = self._make_picking(self.stock_location, self.shelf_1, self.picking_type_int, 3)
        with self.assertRaisesRegex(UserError, 'Insufficient stock'), self.env.cr.savepoint():
            self._validate(picking, self.user_stock_user)
        self.assertNotEqual(picking.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.productA, self.stock_location), 2)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.productA, self.shelf_1), 0)

    def test_exact_stock_and_receipt_succeed(self):
        receipt = self._make_picking(self.supplier_location, self.stock_location, self.picking_type_in, 2)
        self._validate(receipt, self.user_stock_user)
        self.assertEqual(receipt.state, 'done')
        transfer = self._make_picking(self.stock_location, self.shelf_1, self.picking_type_int, 2)
        self._validate(transfer, self.user_stock_user)
        self.assertEqual(transfer.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(
            self.productA, self.stock_location, strict=True), 0)

    def test_delivery_blocks_shortage(self):
        delivery = self._make_picking(self.stock_location, self.customer_location, self.picking_type_out, 1)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._validate(delivery, self.user_stock_user)
        self.assertNotEqual(delivery.state, 'done')

    def test_selected_user_can_make_negative_stock(self):
        self.user_stock_manager.sudo().qing_allow_negative_stock = True
        permitted = self._make_picking(self.stock_location, self.shelf_1, self.picking_type_int, 2)
        self._validate(permitted, self.user_stock_manager)
        self.assertEqual(permitted.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(
            self.productA, self.stock_location, strict=True, allow_negative=True), -2)
        blocked = self._make_picking(self.stock_location, self.shelf_2, self.picking_type_int, 1)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._validate(blocked, self.user_stock_user)

    def test_global_setting_defaults_to_prevent_negative_stock(self):
        parameter = self.env['ir.config_parameter'].sudo()
        self.assertTrue(parameter.get_bool('qing_stock.prevent_negative_stock', default=True))
        settings = self.env['res.config.settings'].sudo().create({})
        self.assertTrue(settings.qing_prevent_negative_stock)
        view = self.env.ref('qing_stock.view_res_config_settings_form_negative_stock').sudo()
        self.assertIn(b'qing_prevent_negative_stock', view.get_combined_arch().encode())

    def test_global_setting_off_allows_negative_stock_for_ordinary_user(self):
        settings = self.env['res.config.settings'].sudo().create({})
        settings.qing_prevent_negative_stock = False
        settings.set_values()
        parameter = self.env['ir.config_parameter'].sudo()
        self.assertFalse(parameter.get_bool('qing_stock.prevent_negative_stock', default=True))
        self.assertFalse(self.env['res.config.settings'].sudo().create({}).qing_prevent_negative_stock)

        transfer = self._make_picking(self.stock_location, self.shelf_1, self.picking_type_int, 1)
        self._validate(transfer, self.user_stock_user)
        self.assertEqual(transfer.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(
            self.productA, self.stock_location, strict=True, allow_negative=True), -1)

        parameter.set_bool('qing_stock.prevent_negative_stock', True)
        blocked = self._make_picking(self.stock_location, self.shelf_2, self.picking_type_int, 1)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._validate(blocked, self.user_stock_user)

    def test_user_cannot_grant_self_exception(self):
        with self.assertRaises(AccessError):
            self.user_stock_user.with_user(self.user_stock_user).write({'qing_allow_negative_stock': True})

    def test_reservation_does_not_count_as_negative_physical_stock(self):
        quant = self.env['stock.quant']
        quant._update_available_quantity(self.productA, self.stock_location, 2)
        quant._update_reserved_quantity(self.productA, self.stock_location, 2)
        quant.with_user(self.user_stock_user)._update_available_quantity(self.productA, self.stock_location, -1)
        self.assertEqual(sum(quant._gather(self.productA, self.stock_location, strict=True).mapped('quantity')), 1)

    def test_direct_internal_deduction_cannot_make_negative_stock(self):
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.env['stock.quant'].with_user(self.user_stock_user)._update_available_quantity(
                self.productA, self.stock_location, -1)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.productA, self.stock_location), 0)

    def test_shortage_message_uses_chinese_translation(self):
        with self.assertRaisesRegex(UserError, '库存不足'), self.env.cr.savepoint():
            self.env['stock.quant'].with_user(self.user_stock_user).with_context(lang='zh_CN')._update_available_quantity(
                self.productA, self.stock_location, -1)
