from datetime import timedelta

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from ..models.common import internal


@tagged('post_install', '-at_install')
class TestRentalStock(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [
            Command.link(cls.env.ref('qing_rental.group_rental_user').id),
            Command.link(cls.env.ref('stock.group_stock_manager').id),
            Command.link(cls.env.ref('sales_team.group_sale_manager').id),
        ]})
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Rental stock tests', 'code': 'RST', 'company_id': cls.env.company.id,
            })
        cls.warehouse.action_qing_setup_rental()
        cls.service = cls._create_product(
            name='Rental stock fee', type='service', invoice_policy='order',
            uom_id=cls.uom_unit.id, taxes_id=[Command.clear()],
        )
        cls.equipment = cls._create_product(
            name='Rental stock equipment', type='consu', is_storable=True,
            uom_id=cls.uom_unit.id, qing_rental_ok=True,
            qing_rental_service_id=cls.service.id, qing_rental_rate=10,
            taxes_id=[Command.clear()],
        )
        cls.stock = cls.warehouse.qing_rental_stock_location_id
        cls.on_rent = cls.warehouse.qing_rental_out_location_id
        cls.env['stock.quant']._update_available_quantity(cls.equipment, cls.stock, 10)
        cls.stock_user = new_test_user(
            cls.env, login='qing_rental_stock_operator', groups='stock.group_stock_user',
            company_id=cls.env.company.id,
        )

    def _order(self, quantity=2, product=None, confirm=True, **values):
        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner_a.id, 'warehouse_id': self.warehouse.id,
            'company_id': self.env.company.id, 'is_qing_rental': True,
            'qing_rental_start': now - timedelta(hours=1), 'qing_rental_end': now + timedelta(days=2),
            'qing_rental_line_ids': [Command.create({'product_id': (product or self.equipment).id, 'quantity': quantity})],
            **values,
        })
        if confirm:
            order.action_confirm()
        return order

    def _transfer(self, order, direction='out'):
        action = order.action_qing_rental_pickup() if direction == 'out' else order.action_qing_rental_return()
        self.assertNotIn('_qing_rental_internal', action.get('context', {}))
        return self.env['stock.picking'].browse(action['res_id'])

    def _validate(self, picking, quantity=None, lot=None, user=None):
        picking = picking.with_user(user or self.env.user)
        move = picking.move_ids
        move.ensure_one()
        if lot:
            move.move_line_ids.unlink()
            self.env['stock.move.line'].with_user(user or self.env.user).create({
                'move_id': move.id, 'picking_id': picking.id, 'product_id': move.product_id.id,
                'uom_id': move.uom_id.id, 'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id, 'quantity': quantity or 1, 'lot_id': lot.id,
            })
        elif quantity is not None:
            move.quantity = quantity
        move.picked = True
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')

    def test_locations_are_separate_and_setup_is_idempotent(self):
        original = (self.stock | self.on_rent).ids
        self.warehouse.action_qing_setup_rental()
        self.assertEqual((self.warehouse.qing_rental_stock_location_id | self.warehouse.qing_rental_out_location_id).ids, original)
        self.assertFalse(self.stock._child_of(self.warehouse.lot_stock_id))
        self.assertFalse(self.on_rent._child_of(self.warehouse.lot_stock_id))
        with self.assertRaises(AccessError):
            self.warehouse.with_user(self.stock_user).action_qing_setup_rental()
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.warehouse.qing_rental_out_location_id = self.warehouse.lot_stock_id

    def test_pickup_and_return_use_native_internal_transfers(self):
        order = self._order()
        pickup = self._transfer(order)
        self.assertEqual(self._transfer(order), pickup, 'Repeated pickup must reuse the pending transfer.')
        self._validate(pickup)
        line = order.qing_rental_line_ids
        self.assertEqual(line.qty_out, 2)
        self.assertEqual(line.qty_returned, 0)
        self.assertEqual(pickup.qing_rental_order_ids, order)
        self.assertEqual(self.equipment.with_context(location=self.stock.id).qty_available, 8)
        returned = self._transfer(order, 'in')
        self._validate(returned)
        self.assertEqual(line.qty_returned, 2)
        self.assertEqual(self.equipment.with_context(location=self.stock.id).qty_available, 10)
        self.assertEqual(order.qing_rental_status, 'returned')

    def test_partial_pickup_and_return_preserve_links_on_backorders(self):
        order = self._order(quantity=3)
        pickup = self._transfer(order)
        self._validate(pickup, quantity=1)
        backorder = pickup.backorder_ids
        self.assertEqual(len(backorder), 1)
        self.assertEqual(backorder.move_ids.qing_rental_line_id, order.qing_rental_line_ids)
        self.assertEqual(backorder.move_ids.qing_rental_direction, 'out')
        self.assertEqual(backorder.move_ids.product_uom_qty, 2)
        self._validate(backorder)
        self.assertEqual(order.qing_rental_line_ids.qty_out, 3)
        returned = self._transfer(order, 'in')
        self._validate(returned, quantity=1)
        self.assertEqual(order.qing_rental_line_ids.qty_returned, 1)
        self.assertEqual(returned.backorder_ids.move_ids.qing_rental_direction, 'in')
        self._validate(returned.backorder_ids)
        self.assertEqual(order.qing_rental_line_ids.qty_returned, 3)

    def test_partial_return_releases_only_returned_units_after_turnaround(self):
        self.equipment.qing_rental_padding_hours = 2
        order = self._order(quantity=10)
        self._validate(self._transfer(order))
        self._validate(self._transfer(order, 'in'), quantity=3)
        now = fields.Datetime.now()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._order(quantity=1, qing_rental_start=now + timedelta(hours=1),
                        qing_rental_end=now + timedelta(hours=2))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._order(quantity=4, qing_rental_start=now + timedelta(hours=3),
                        qing_rental_end=now + timedelta(hours=4))
        next_order = self._order(quantity=3, qing_rental_start=now + timedelta(hours=3),
                                 qing_rental_end=now + timedelta(hours=4))
        self.assertEqual(next_order.state, 'sale')
        self.assertEqual(order.qing_rental_line_ids.qty_returned, 3)

    def test_partial_return_renewal_charges_only_remaining_units(self):
        order = self._order(quantity=10)
        original_charge = order.amount_untaxed
        self._validate(self._transfer(order))
        self._validate(self._transfer(order, 'in'), quantity=3)
        renewal = self.env['qing.rental.renew'].create({
            'order_id': order.id, 'new_end': order.qing_rental_end + timedelta(days=1),
        })
        self.assertEqual(renewal.charge_amount, 70)
        renewal.action_apply()
        charge = order.order_line.filtered(lambda line: line.qing_rental_charge == 'renewal')
        self.assertEqual(charge.product_uom_qty, 7)
        self.assertEqual(charge.price_unit, 10)
        self.assertEqual(order.amount_untaxed, original_charge + 70)

    def test_stock_operator_validates_without_rental_or_invoice_role(self):
        order = self._order()
        stock_env = order.with_user(self.stock_user).env
        self.assertFalse(stock_env.user.has_group('qing_rental.group_rental_user'))
        # Native sale_stock grants warehouse users read/write on sale orders;
        # this module preserves that upstream policy, and protects its own data.
        with self.assertRaises(AccessError):
            order.qing_rental_line_ids.with_user(self.stock_user).read(['rate'])
        with self.assertRaises(AccessError):
            stock_env['qing.rental.deposit'].check_access('read')
        with self.assertRaises(AccessError):
            stock_env['account.move'].check_access('create')
        with self.assertRaises(AccessError):
            order.with_user(self.stock_user).action_confirm()
        pickup = self._transfer(order)
        self._validate(pickup, user=self.stock_user)
        self._validate(self._transfer(order, 'in'), user=self.stock_user)
        self.assertEqual(order.qing_rental_line_ids.qty_returned, 2)

    def test_overpickup_rolls_back_inventory(self):
        order = self._order()
        pickup = self._transfer(order)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._validate(pickup, quantity=3)
        self.assertNotEqual(pickup.state, 'done')
        self.assertEqual(order.qing_rental_line_ids.qty_out, 0)
        self.assertEqual(self.equipment.with_context(location=self.stock.id).qty_available, 10)

    def test_insufficient_physical_inventory_after_pickup_creation(self):
        order = self._order()
        pickup = self._transfer(order)
        self.env['stock.quant']._update_available_quantity(self.equipment, self.stock, -9)
        with self.assertRaises((UserError, ValidationError)), self.env.cr.savepoint():
            self._validate(pickup)
        self.assertEqual(order.qing_rental_line_ids.qty_out, 0)

    def test_overreturn_rolls_back_inventory(self):
        order = self._order()
        self._validate(self._transfer(order))
        returned = self._transfer(order, 'in')
        with self.assertRaises((UserError, ValidationError)), self.env.cr.savepoint():
            self._validate(returned, quantity=3)
        self.assertEqual(order.qing_rental_line_ids.qty_returned, 0)

    def test_serial_return_cannot_take_another_orders_equipment(self):
        serial_product = self.equipment.copy({'name': 'Serial rental equipment', 'tracking': 'serial'})
        lots = self.env['stock.lot'].create([
            {'name': 'RENT-SERIAL-1', 'product_id': serial_product.id, 'company_id': self.env.company.id},
            {'name': 'RENT-SERIAL-2', 'product_id': serial_product.id, 'company_id': self.env.company.id},
        ])
        for lot in lots:
            self.env['stock.quant']._update_available_quantity(serial_product, self.stock, 1, lot_id=lot)
        first, second = self._order(quantity=1, product=serial_product), self._order(quantity=1, product=serial_product)
        self._validate(self._transfer(first), lot=lots[0])
        self._validate(self._transfer(second), lot=lots[1])
        returned = self._transfer(first, 'in')
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._validate(returned, lot=lots[1])
        self.assertEqual(first.qing_rental_line_ids.qty_returned, 0)
        self._validate(returned, lot=lots[0])
        self.assertEqual(first.qing_rental_line_ids.qty_returned, 1)
        self.assertEqual(second.qing_rental_line_ids.qty_returned, 0)

    def test_rental_links_and_completed_quantities_cannot_be_forged(self):
        order = self._order()
        pickup = self._transfer(order)
        move = pickup.move_ids
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            move.write({'qing_rental_line_id': False})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            move.copy()
        values = {'product_id': self.equipment.id, 'product_uom_qty': 1, 'uom_id': self.uom_unit.id,
                  'location_id': self.stock.id, 'location_dest_id': self.on_rent.id,
                  'company_id': self.env.company.id, 'qing_rental_line_id': order.qing_rental_line_ids.id,
                  'qing_rental_direction': 'out'}
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env['stock.move'].with_context(_qing_rental_internal=True).create(values)
        defaults = {'default_qing_rental_line_id': values.pop('qing_rental_line_id'),
                    'default_qing_rental_direction': values.pop('qing_rental_direction')}
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env['stock.move'].with_context(**defaults).create(values)
        self._validate(pickup)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            move.quantity = 1
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            move.move_line_ids.write({'quantity': 1})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            move.move_line_ids.unlink()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            pickup._create_return()

    def test_manual_transfer_cannot_bypass_the_rental_ledger(self):
        move = self.env['stock.move'].create({
            'product_id': self.equipment.id, 'product_uom_qty': 1, 'uom_id': self.uom_unit.id,
            'location_id': self.stock.id, 'location_dest_id': self.on_rent.id,
            'company_id': self.env.company.id,
        })
        move._action_confirm()
        move.quantity = 1
        move.picked = True
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            move._action_done()
        move.is_inventory = True
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            move._action_done()

    def test_native_inventory_adjustment_in_on_rent_location_remains_available(self):
        order = self._order()
        self._validate(self._transfer(order))
        Quant = self.env['stock.quant']
        quant = Quant.search([('product_id', '=', self.equipment.id), ('location_id', '=', self.on_rent.id)])
        self.assertEqual(quant.quantity, 2)
        quant.inventory_quantity = 1
        quant.action_apply_inventory()
        self.assertEqual(quant.quantity, 1)
        self.assertEqual(order.qing_rental_line_ids.qty_out, 2, 'Physical adjustments do not fabricate rental returns.')
        self.assertEqual(order.qing_rental_line_ids.qty_returned, 0)

    def test_existing_operation_cannot_be_moved_into_a_rental(self):
        order = self._order()
        pickup = self._transfer(order)
        other = self.env['stock.move'].create({
            'product_id': self.equipment.id, 'product_uom_qty': 1, 'uom_id': self.uom_unit.id,
            'location_id': self.stock.id, 'location_dest_id': self.warehouse.lot_stock_id.id,
            'company_id': self.env.company.id,
        })
        other._action_confirm()
        other.quantity = 1
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            other.move_line_ids.write({'move_id': pickup.move_ids.id})

    def test_expired_pending_pickup_cannot_be_validated(self):
        order = self._order()
        pickup = self._transfer(order)
        internal(order).write({'qing_rental_end': fields.Datetime.now() - timedelta(minutes=1)})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._validate(pickup)

    def test_warehouse_locations_cannot_change_after_rental_transfers(self):
        order = self._order()
        self._transfer(order)
        replacement = self.stock.copy({'name': 'Replacement rental stock'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.warehouse.qing_rental_stock_location_id = replacement

    def test_opening_quote_composer_and_preview_refreshes_rental_prices(self):
        order = self._order(confirm=False)
        line = order.qing_rental_line_ids
        self.assertFalse(order.order_line)
        composer = order.action_quotation_send()
        self.assertEqual(composer['res_model'], 'mail.compose.message')
        self.assertEqual(order.amount_untaxed, line.amount)
        self.assertEqual(order.order_line.qing_rental_line_id, line)
        line.rate = 17
        order.action_quotation_send()
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.amount_untaxed, line.amount)
        self.assertEqual(order.order_line.qing_rental_billed_start, order.qing_rental_start)
        self.assertEqual(order.order_line.qing_rental_billed_end, order.qing_rental_end)
        line.rate = 21
        preview = order.action_preview_sale_order()
        self.assertEqual(preview['type'], 'ir.actions.act_url')
        self.assertEqual(order.amount_untaxed, line.amount)
        self.assertEqual(order.state, 'draft', 'Opening the composer must not send a message or confirm the order.')

    def test_reports_reject_missing_or_stale_rental_charges(self):
        order = self._order(confirm=False)
        Report = self.env['ir.actions.report']
        reports = ('sale.action_report_saleorder', 'sale.action_report_pro_forma_invoice')
        for report_ref in reports:
            with self.subTest(report_ref=report_ref), self.assertRaises(UserError), self.env.cr.savepoint():
                Report._render_qweb_html(report_ref, order.ids)
        order.action_qing_rental_reprice()
        html, output_type = Report._render_qweb_html(reports[0], order.ids)
        self.assertEqual(output_type, 'html')
        self.assertIn(order.name, html.decode() if isinstance(html, bytes) else html)
        old_amount = order.amount_untaxed
        order.qing_rental_start += timedelta(minutes=1)
        self.assertEqual(order.qing_rental_line_ids.amount, old_amount, 'This date edit stays within the same billed period.')
        for report_ref in reports:
            with self.subTest(report_ref=report_ref), self.assertRaises(UserError), self.env.cr.savepoint():
                Report._render_qweb_html(report_ref, order.ids)
        order.action_qing_rental_reprice()
        html, output_type = Report._render_qweb_html(reports[1], order.ids)
        self.assertEqual(output_type, 'html')
        self.assertIn(order.name, html.decode() if isinstance(html, bytes) else html)

    def test_ordinary_sales_send_preview_and_report_keep_native_behavior(self):
        sales_user = new_test_user(
            self.env, login='ordinary_quote_no_rental',
            groups='sales_team.group_sale_salesman,base.group_partner_manager',
            company_id=self.env.company.id,
        )
        team = self.env['crm.team'].create({
            'name': 'Ordinary quotation team',
            'member_ids': [Command.set(sales_user.ids)],
        })
        order = self.env['sale.order'].create({
            'partner_id': self.partner_a.id, 'warehouse_id': self.warehouse.id,
            'company_id': self.env.company.id, 'user_id': sales_user.id,
            'team_id': team.id,
            'order_line': [Command.create({'product_id': self.service.id, 'product_uom_qty': 2, 'price_unit': 12})],
        }).with_user(sales_user)
        self.assertFalse(order.is_qing_rental)
        self.assertFalse(order.env.user.has_group('qing_rental.group_rental_user'))
        self.assertEqual(order.action_quotation_send()['res_model'], 'mail.compose.message')
        self.assertEqual(order.action_preview_sale_order()['type'], 'ir.actions.act_url')
        html, output_type = order.env['ir.actions.report']._render_qweb_html('sale.action_report_saleorder', order.ids)
        self.assertEqual(output_type, 'html')
        self.assertIn(order.name, html.decode() if isinstance(html, bytes) else html)
        self.assertEqual(order.amount_untaxed, 24)
        self.assertFalse(order.order_line.qing_rental_line_id)
