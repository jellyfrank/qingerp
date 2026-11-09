import uuid

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestWarehouseScan(TransactionCase):
    def test_mobile_internal_native_form_draft_and_execution(self):
        self.warehouse.int_type_id.active = True
        picking_model = self.env['stock.picking'].with_user(self.operator)
        action = picking_model.action_qing_new_internal()
        dest = self.env['stock.location'].create({'name': 'Mobile destination', 'location_id': self.stock.id, 'usage': 'internal'})
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 10)
        form = Form(picking_model.with_context(action['context']), view=action['views'][0][0])
        form.picking_type_id = self.warehouse.int_type_id
        form.location_id = self.stock
        form.location_dest_id = dest
        with form.move_ids.new() as line:
            line.product_id = self.product
            line.product_uom_qty = 4
        transfer = form.save()
        self.assertEqual(transfer.state, 'draft')
        self.assertFalse(transfer.move_ids.picked)
        self.assertEqual(transfer.move_ids.quantity, 0)
        saved = transfer.action_qing_save_draft()
        self.assertTrue(saved['params']['list_only'])
        dashboard = self.Session.with_user(self.operator).dashboard('internal')
        self.assertTrue(dashboard['can_create_internal'])
        self.assertEqual(next(r['state'] for r in dashboard['transfers'] if r['id'] == transfer.id), 'draft')
        self.assertEqual(transfer.action_qing_edit_internal()['res_id'], transfer.id)
        action = transfer.action_qing_confirm_scan()
        self.assertEqual(action['params']['picking_id'], transfer.id)
        self.assertFalse(action['params']['reservation_warning'])
        self.assertEqual(transfer.state, 'assigned')
        self.assertFalse(transfer.move_ids.picked)
        self.assertEqual(transfer.action_qing_confirm_scan()['params']['picking_id'], transfer.id)
        session = self.start(transfer, self.operator)
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 0)
        self.assertTrue(self.scan(session, quantity=4)['ok'])
        self.finish(session)
        self.assertEqual(transfer.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, dest), 4)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            transfer.action_qing_edit_internal()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            transfer.action_qing_confirm_scan()

    def test_mobile_internal_validation_permissions_and_shortage(self):
        self.warehouse.int_type_id.active = True
        model = self.env['stock.picking'].with_user(self.operator)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env['stock.picking'].with_user(self.env.ref('base.public_user')).action_qing_new_internal()
        dest = self.env['stock.location'].create({'name': 'Mobile target', 'location_id': self.stock.id, 'usage': 'internal'})
        transfer = model.create({'picking_type_id': self.warehouse.int_type_id.id, 'location_id': self.stock.id, 'location_dest_id': self.stock.id})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            transfer.action_qing_confirm_scan()
        transfer.location_dest_id = dest
        with self.assertRaises(UserError), self.env.cr.savepoint():
            transfer.action_qing_confirm_scan()
        transfer.write({'move_ids': [Command.create({'product_id': self.product.id, 'product_uom_qty': 0, 'uom_id': self.product.uom_id.id, 'location_id': self.stock.id, 'location_dest_id': dest.id})]})
        for qty in (0, -1):
            transfer.move_ids.product_uom_qty = qty
            with self.assertRaises(UserError), self.env.cr.savepoint():
                transfer.action_qing_confirm_scan()
        transfer.move_ids.product_uom_qty = 3
        self.assertTrue(transfer.action_qing_confirm_scan()['params']['reservation_warning'])
        self.assertFalse(transfer.move_ids.picked)
        self.assertEqual(transfer.move_ids.quantity, 0)
        self.assertFalse(self.Session.with_user(self.operator).dashboard('incoming')['can_create_internal'])
        incoming = self.make_picking()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            incoming.with_user(self.operator).action_qing_confirm_scan()
        company = self.env['res.company'].create({'name': 'Other mobile company'})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            transfer.with_user(self.env.user).with_company(company).action_qing_confirm_scan()

    def test_mobile_draft_single_line_edit_scaling(self):
        import logging
        counts = []
        for size in (50, 150):
            with self.env.cr.savepoint():
                dest = self.env['stock.location'].create({'name': 'Scale target', 'location_id': self.stock.id, 'usage': 'internal'})
                transfer = self.env['stock.picking'].create({
                    'picking_type_id': self.warehouse.int_type_id.id, 'location_id': self.stock.id, 'location_dest_id': dest.id,
                    'move_ids': [Command.create({'product_id': self.product.id, 'product_uom_qty': 10,
                        'uom_id': self.product.uom_id.id, 'location_id': self.stock.id, 'location_dest_id': dest.id}) for _ in range(size)],
                })
                self.env.flush_all()
                target = transfer.move_ids[0]
                siblings = transfer.move_ids - target
                stamps = dict(zip(siblings.ids, siblings.mapped('write_date')))
                before = self.env.cr.sql_log_count
                target.product_uom_qty = 11
                self.env.flush_all()
                counts.append(self.env.cr.sql_log_count - before)
                self.assertEqual(set(siblings.mapped('product_uom_qty')), {10})
                self.assertEqual(dict(zip(siblings.ids, siblings.mapped('write_date'))), stamps)
        logging.getLogger(__name__).info('Mobile draft 50/150 single-line edit SQL: %s; sibling writes: 0', counts)
        self.assertLessEqual(counts[1], counts[0] + 10, counts)

    def test_dashboard_progress_resumes_and_respects_visibility(self):
        transfer = self.make_picking(qty=10)
        transfer.origin = 'Active progress fixture'
        session = self.start(transfer, self.operator)
        self.assertTrue(self.scan(session, quantity=5)['ok'])
        model = self.Session.with_user(self.operator)
        row = next(row for row in model.dashboard()['transfers'] if row['id'] == transfer.id)
        self.assertEqual(row['progress'], 50)
        self.assertEqual(row['session_id'], session.id)
        self.assertEqual(model.start_picking(transfer.id, str(uuid.uuid4()))['id'], session.id)
        hidden = next(row for row in self.Session.with_user(self.second).dashboard()['transfers'] if row['id'] == transfer.id)
        self.assertFalse(hidden['session_id'])
        self.assertEqual(hidden['progress'], 0)
        self.assertFalse(model.dashboard(search='No matching fixture')['transfers'])
        self.assertNotIn(transfer.id, [row['id'] for row in model.dashboard('outgoing')['transfers']])
        self.assertTrue(session.execute(str(uuid.uuid4()), {'op': 'cancel'})['ok'])
        row = next(row for row in model.dashboard()['transfers'] if row['id'] == transfer.id)
        self.assertFalse(row['session_id'])
        self.assertEqual(row['progress'], 0)

    def test_location_progress_keeps_review_session_reachable(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 10)
        self.env['stock.quant']._update_available_quantity(self.other, self.stock, 10)
        model = self.Session.with_user(self.operator)
        session = model.browse(model.start_count(self.stock.id, str(uuid.uuid4()))['id'])
        line = session.line_ids.filtered(lambda line: line.product_id == self.product)
        self.assertTrue(session.execute(str(uuid.uuid4()), {'op': 'set_count', 'line_id': line.id, 'quantity': 0})['ok'])
        self.assertTrue(session.execute(str(uuid.uuid4()), {'op': 'submit_review'})['ok'])
        row = next(row for row in model.find_locations() if row['id'] == self.stock.id)
        self.assertEqual(row['session_id'], session.id)
        self.assertEqual(row['progress'], 50)
        self.assertEqual(model.start_count(self.stock.id, str(uuid.uuid4()))['state'], 'review')

    def test_list_progress_prefetch_for_50_and_150_moves(self):
        from ..services.common import internal
        counts = []
        for size in (50, 150):
            transfer = self.env['stock.picking'].create({
                'picking_type_id': self.warehouse.in_type_id.id,
                'location_id': self.supplier.id, 'location_dest_id': self.stock.id,
                'move_ids': [Command.create({'product_id': self.product.id, 'product_uom_qty': 10,
                    'uom_id': self.product.uom_id.id, 'location_id': self.supplier.id,
                    'location_dest_id': self.stock.id}) for _ in range(size)],
            })
            transfer.move_ids._action_confirm(merge=False)
            session = self.start(transfer, self.operator)
            self.assertEqual(len(session.line_ids), size)
            internal(session.line_ids).write({'quantity': 5})
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            result = session._list_progress()
            counts.append(self.env.cr.sql_log_count - before)
            self.assertEqual(result[session.id], 50)
        self.assertLessEqual(counts[1], counts[0] + 5, counts)

    def test_dashboard_completed_rows_cannot_start_again(self):
        completed = self.make_picking(qty=1)
        session = self.start(completed, self.operator)
        self.scan(session, 'SCAN-A')
        self.finish(session)
        pending = self.make_picking()
        cancelled = self.make_picking()
        cancelled.action_cancel()
        model = self.Session.with_user(self.operator)
        rows = model.dashboard()['transfers']
        ids = [row['id'] for row in rows]
        self.assertIn(completed.id, ids)
        self.assertLess(ids.index(pending.id), ids.index(completed.id))
        self.assertNotIn(cancelled.id, ids)
        self.assertEqual(next(row for row in rows if row['id'] == completed.id)['state'], 'done')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            model.start_picking(completed.id, str(uuid.uuid4()))
        self.assertEqual(completed.state, 'done')

    def test_completed_native_details_are_readonly_and_company_scoped(self):
        model = self.Session.with_user(self.second)
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 10)
        for direction in ('incoming', 'outgoing', 'internal'):
            transfer = self.make_picking(qty=2, direction='incoming' if direction == 'internal' else direction)
            if direction == 'internal':
                transfer.picking_type_id = self.warehouse.int_type_id
            transfer.move_ids.quantity = 2
            transfer.move_ids.picked = True
            transfer.button_validate()
            self.assertEqual(transfer.state, 'done')
            before = self.Session.search_count([])
            write_date = transfer.write_date
            data = model.get_completed_picking(transfer.id)
            self.assertTrue(data['readonly'])
            self.assertFalse(data['id'])
            self.assertEqual(data['operation'], direction)
            self.assertEqual(sum(line['quantity'] for line in data['lines']), 2)
            self.assertEqual(data['moves'][0]['scanned'], 2)
            self.assertEqual(self.Session.search_count([]), before)
            self.assertEqual(transfer.write_date, write_date)
            self.assertIn(transfer.id, [row['id'] for row in model.dashboard(direction)['transfers']])
            self.assertNotIn(transfer.id, [row['id'] for row in model.dashboard(direction, show_completed=False)['transfers']])
            row = next(row for row in model.dashboard(direction)['transfers'] if row['id'] == transfer.id)
            self.assertEqual(row['progress'], 100)
        pending = self.make_picking()
        with self.assertRaises(UserError):
            model.get_completed_picking(pending.id)
        other_company = self.env['res.company'].create({'name': 'Other completed company'})
        with self.assertRaises(AccessError):
            self.Session.with_company(other_company).get_completed_picking(transfer.id)
        with self.assertRaises(AccessError):
            self.Session.with_user(self.env.ref('base.public_user')).get_completed_picking(transfer.id)

    def test_completed_view_does_not_require_other_operators_scan_session(self):
        transfer = self.make_picking(qty=3)
        scan = self.start(transfer, self.operator)
        self.scan(scan, quantity=3)
        self.finish(scan)
        with self.assertRaises(AccessError):
            scan.with_user(self.second).get_session()
        data = self.Session.with_user(self.second).get_completed_picking(transfer.id)
        self.assertEqual(data['moves'][0]['scanned'], 3)
        self.assertTrue(data['readonly'])
        self.assertFalse(data['events'])

    def test_dashboard_responsible_is_the_transfer_owner(self):
        picking = self.make_picking()
        picking.user_id = self.operator
        row = next(row for row in self.Session.with_user(self.operator).dashboard()['transfers'] if row['id'] == picking.id)
        self.assertEqual(row['responsible'], self.operator.display_name)
        self.assertEqual(row['source'], picking.location_id.display_name)
        self.assertEqual(row['destination'], picking.location_dest_id.display_name)
        session = self.start(picking, self.second)
        self.assertEqual(session.get_session()['responsible'], self.operator.display_name)
        picking.user_id = False
        self.assertEqual(session.get_session()['responsible'], '')
        row = next(row for row in self.Session.with_user(self.operator).dashboard()['transfers'] if row['id'] == picking.id)
        self.assertEqual(row['responsible'], '')

    def test_sku_without_barcode_receipt_inventory_and_lookup(self):
        self.product.write({'barcode': False, 'default_code': 'FURN_7777'})
        session = self.start(self.make_picking(qty=2), self.operator)
        for _ in range(2):
            result = self.scan(session, 'FURN_7777')
            self.assertTrue(result['ok'], result)
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 2)
        self.finish(session)
        lookup = self.Session.with_user(self.operator).lookup_stock('FURN_7777')
        self.assertEqual(sum(row['quantity'] for row in lookup['rows']), 2)
        model = self.Session.with_user(self.operator)
        count = model.browse(model.start_count(self.stock.id, str(uuid.uuid4()))['id'])
        self.assertTrue(self.scan(count, 'FURN_7777')['ok'])
        self.assertEqual(count.line_ids.filtered(lambda line: line.product_id == self.product).quantity, 1)

    def test_sku_collisions_do_not_select_a_product_or_unit_silently(self):
        self.product.default_code = 'SKU-COLLISION'
        self.other.default_code = 'SKU-COLLISION'
        session = self.start(self.make_picking(), self.operator)
        self.assertFalse(self.scan(session, 'SKU-COLLISION', kind='product')['ok'])
        self.other.write({'default_code': False, 'barcode': 'SKU-COLLISION'})
        self.assertFalse(self.scan(session, 'SKU-COLLISION', kind='product')['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 0)
        self.other.barcode = 'SCAN-B'
        self.product.barcode = 'SKU-COLLISION'
        # A single product matching both its barcode and SKU still counts exactly once.
        self.assertTrue(self.scan(session, 'SKU-COLLISION')['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 1)
        self.product.barcode = 'SCAN-A'
        carton = self.env['uom.uom'].create({'name': 'SKU carton', 'relative_uom_id': self.product.uom_id.id, 'relative_factor': 6})
        self.env['product.uom'].create({'product_id': self.product.id, 'uom_id': carton.id, 'barcode': 'SKU-COLLISION'})
        self.assertFalse(self.scan(session, 'SKU-COLLISION')['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 1)

    def test_sku_exact_match_respects_company_and_active_products(self):
        self.product.default_code = 'FURN_7777'
        other_company = self.env['res.company'].create({'name': 'Other SKU company'})
        self.env['product.product'].create({'name': 'Other company SKU', 'default_code': 'FURN_7777',
                                          'company_id': other_company.id, 'is_storable': True})
        self.other.write({'default_code': 'FURN_7777', 'active': False})
        session = self.start(self.make_picking(), self.operator)
        self.assertFalse(self.scan(session, 'FURN_777')['ok'])
        self.assertFalse(self.scan(session, 'furn_7777')['ok'])
        self.assertTrue(self.scan(session, 'FURN_7777')['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 1)

    def test_serial_rechecked_after_backorder_dialog(self):
        first = self.start(self.make_picking(product=self.serial, qty=2))
        self.scan(first, 'SCAN-S')
        self.scan(first, 'SERIAL-WAIT')
        pending = first.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertTrue(pending['ok'])
        self.assertEqual(first.state, 'applied')
        self.warehouse.in_type_id.use_existing_lots = True
        other = self.start(self.make_picking(product=self.serial, qty=1))
        self.scan(other, 'SCAN-S')
        self.scan(other, 'SERIAL-WAIT')
        self.finish(other)
        wizard = self.env['stock.backorder.confirmation'].with_context(pending['result']['action']['context']).create({})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.process()
        self.assertNotEqual(first.picking_id.state, 'done')

    def test_ambiguous_codes_pending_lot_and_lot_lookup(self):
        transfer = self.make_picking(product=self.lot_product, qty=2)
        session = self.start(transfer)
        self.assertTrue(self.scan(session, 'SCAN-L')['ok'])
        self.assertFalse(self.scan(session, 'SCAN-A')['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 0)
        self.assertTrue(self.scan(session, 'LOT-QUERY')['ok'])
        self.finish(session)
        lookup = self.Session.lookup_stock('LOT-QUERY')
        self.assertEqual(sum(row['quantity'] for row in lookup['rows']), 1)
        self.stock.barcode = 'SCAN-A'
        other = self.start(self.make_picking())
        self.assertFalse(self.scan(other)['ok'])
        self.assertTrue(self.scan(other, kind='product')['ok'])

    def test_manager_release_preserves_native_quantities(self):
        transfer = self.make_picking()
        session = self.start(transfer, self.operator)
        self.scan(session, quantity=8)
        result = session.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertTrue(result['ok'])

        self.assertEqual(session.state, 'applied')
        transfer.move_ids.product_uom_qty = 13
        self.assertFalse(session.execute(str(uuid.uuid4()), {'op': 'finish'})['ok'])
        self.assertFalse(session.execute(str(uuid.uuid4()), {'op': 'release'})['ok'])
        self.assertTrue(session.with_user(self.env.user).execute(str(uuid.uuid4()), {'op': 'release'})['ok'])
        self.assertEqual(transfer.move_line_ids.quantity, 8)
        self.assertEqual(session.state, 'cancelled')
        self.assertFalse(session.active_key)

    def test_invalid_quantities_and_insufficient_stock(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 12)
        transfer = self.make_picking(direction='outgoing')
        session = self.start(transfer)
        digits = self.env['decimal.precision'].precision_get('Product Unit')
        for amount in (True, -1, 0, 10 ** (-digits - 1), 1e10, '2'):
            self.assertFalse(self.scan(session, quantity=amount)['ok'], amount)
        self.assertTrue(self.scan(session, quantity=8)['ok'])
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, -10)
        result = session.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertFalse(result['ok'])
        self.assertNotEqual(transfer.state, 'done')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.supplier = cls.env.ref('stock.stock_location_suppliers')
        cls.customer = cls.env.ref('stock.stock_location_customers')
        cls.product = cls.env['product.product'].create({'name': 'Scan Widget', 'is_storable': True, 'barcode': 'SCAN-A'})
        cls.other = cls.env['product.product'].create({'name': 'Other Widget', 'is_storable': True, 'barcode': 'SCAN-B'})
        cls.serial = cls.env['product.product'].create({'name': 'Serial Widget', 'is_storable': True, 'barcode': 'SCAN-S', 'tracking': 'serial'})
        cls.lot_product = cls.env['product.product'].create({'name': 'Lot Widget', 'is_storable': True, 'barcode': 'SCAN-L', 'tracking': 'lot'})
        cls.Session = cls.env['qing.stock.scan.session']
        user_env = cls.env(context=dict(cls.env.context, no_reset_password=True))
        cls.operator = new_test_user(user_env, login='scan_operator', groups='qing_stock_barcode.group_scan_user')
        cls.second = new_test_user(user_env, login='scan_second', groups='qing_stock_barcode.group_scan_user')
        cls.reviewer = new_test_user(user_env, login='scan_reviewer', groups='qing_stock_barcode.group_scan_reviewer')

    def make_picking(self, product=None, qty=12, direction='incoming'):
        product = product or self.product
        operation = self.warehouse.in_type_id if direction == 'incoming' else self.warehouse.out_type_id
        source = self.supplier if direction == 'incoming' else self.stock
        destination = self.stock if direction == 'incoming' else self.customer
        transfer = self.env['stock.picking'].create({
            'picking_type_id': operation.id, 'location_id': source.id, 'location_dest_id': destination.id,
            'move_ids': [Command.create({'product_id': product.id, 'product_uom_qty': qty,
                                         'uom_id': product.uom_id.id, 'location_id': source.id, 'location_dest_id': destination.id})],
        })
        transfer.action_confirm()
        transfer.action_assign()
        return transfer

    def start(self, transfer, user=None):
        model = self.Session.with_user(user) if user else self.Session
        result = model.start_picking(transfer.id, str(uuid.uuid4()), 'test-device')
        return model.browse(result['id'])

    def scan(self, session, code='SCAN-A', **kwargs):
        return session.execute(str(uuid.uuid4()), {'op': 'scan', 'code': code, **kwargs})

    def finish(self, session):
        result = session.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertTrue(result['ok'], result)
        action = result['result'].get('action')
        if action and action.get('res_model') == 'stock.backorder.confirmation':
            wizard = self.env['stock.backorder.confirmation'].with_user(session.env.user).with_context(action['context']).create({})
            wizard.process()
        return result

    def test_retry_and_real_repeated_scans(self):
        session = self.start(self.make_picking())
        key = str(uuid.uuid4())
        first = session.execute(key, {'op': 'scan', 'code': 'SCAN-A'})
        for _ in range(10):
            self.assertEqual(session.execute(key, {'op': 'scan', 'code': 'SCAN-A'})['event_id'], first['event_id'])
        self.assertTrue(self.scan(session)['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 2)
        with self.assertRaises(UserError):
            session.execute(key, {'op': 'scan', 'code': 'SCAN-B'})

    def test_receipt_partial_does_not_complete_reserved_quantity(self):
        transfer = self.make_picking()
        session = self.start(transfer)
        self.assertEqual(sum(transfer.move_line_ids.mapped('quantity')), 12)
        self.assertTrue(self.scan(session, quantity=8)['ok'])
        result = self.finish(session)
        self.assertEqual(transfer.state, 'done')
        self.assertEqual(sum(transfer.move_line_ids.mapped('quantity')), 8)
        backorders = self.env['stock.picking'].search([('backorder_id', '=', transfer.id)])
        self.assertEqual(sum(backorders.move_ids.mapped('product_uom_qty')), 4)
        progress = session.get_session()
        self.assertEqual(progress['moves'][0]['planned'], 12)
        self.assertEqual(progress['moves'][0]['scanned'], 8)
        self.assertEqual(progress['backorders'][0]['id'], backorders.id)
        self.assertFalse(progress['backorder_of'])
        backorder_session = self.start(backorders)
        self.assertEqual(backorder_session.get_session()['backorder_of'], transfer.name)
        self.assertEqual(session.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.stock), 8)
        self.assertTrue(result['ok'])

    def test_outgoing_partial_and_unscanned_product(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 12)
        self.env['stock.quant']._update_available_quantity(self.other, self.stock, 4)
        transfer = self.make_picking(direction='outgoing')
        move = self.env['stock.move'].create({'picking_id': transfer.id, 'product_id': self.other.id,
                                             'product_uom_qty': 4, 'uom_id': self.other.uom_id.id,
                                             'location_id': self.stock.id, 'location_dest_id': self.customer.id})
        move._action_confirm()
        transfer.action_assign()
        session = self.start(transfer)
        self.assertTrue(self.scan(session, quantity=8)['ok'])
        self.finish(session)
        self.assertEqual(transfer.state, 'done')
        self.assertEqual(sum(transfer.move_line_ids.mapped('quantity')), 8)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.other, self.customer), 0)

    def test_wrong_code_over_quantity_undo_and_direct_write(self):
        session = self.start(self.make_picking(qty=2))
        self.assertFalse(self.scan(session, 'SCAN-B')['ok'])
        self.assertFalse(self.scan(session, quantity=3)['ok'])
        self.assertTrue(self.scan(session)['ok'])
        self.assertTrue(session.execute(str(uuid.uuid4()), {'op': 'undo'})['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 0)
        with self.assertRaises(AccessError):
            session.line_ids.write({'quantity': 999})
        with self.assertRaises(AccessError):
            session.with_context(_qing_scan_internal=True).write({'state': 'done'})
        with self.assertRaises(AccessError):
            self.env['qing.stock.scan.event'].create({'session_id': session.id})

    def test_native_change_detected_and_native_validation_guarded(self):
        transfer = self.make_picking()
        session = self.start(transfer)
        self.assertTrue(self.scan(session)['ok'])
        with self.assertRaises(UserError):
            transfer.button_validate()
        transfer.move_ids.product_uom_qty = 13
        self.assertFalse(self.scan(session)['ok'])
        result = session.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertFalse(result['ok'])
        self.assertNotEqual(transfer.state, 'done')

    def test_serial_receipt_and_duplicate(self):
        transfer = self.make_picking(self.serial, qty=2)
        session = self.start(transfer)
        self.assertTrue(self.scan(session, 'SCAN-S')['ok'])
        self.assertTrue(session.pending_product_id)
        result = self.scan(session, 'SER-001')
        self.assertTrue(result['ok'], result)
        self.assertTrue(self.scan(session, 'SCAN-S')['ok'])
        self.assertFalse(self.scan(session, 'SER-001')['ok'])
        self.assertTrue(self.scan(session, 'SER-002')['ok'])
        self.finish(session)
        self.assertEqual(set(transfer.move_line_ids.lot_id.mapped('name')), {'SER-001', 'SER-002'})
        self.assertEqual(sum(transfer.move_line_ids.mapped('quantity')), 2)

    def test_operator_ownership_and_reviewer_read(self):
        transfer = self.make_picking()
        session = self.start(transfer, self.operator)
        self.assertTrue(self.scan(session)['ok'])
        with self.assertRaises((AccessError, UserError)):
            self.start(transfer, self.second)
        with self.assertRaises(AccessError):
            session.with_user(self.second).get_session()
        self.assertEqual(session.with_user(self.reviewer).get_session()['id'], session.id)
        with self.assertRaises(AccessError):
            session.with_user(self.reviewer).execute(str(uuid.uuid4()), {'op': 'scan', 'code': 'SCAN-A'})

    def test_inventory_zero_uncounted_and_conflict(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 10)
        self.env['stock.quant']._update_available_quantity(self.other, self.stock, 7)
        data = self.Session.start_count(self.stock.id, str(uuid.uuid4()))
        session = self.Session.browse(data['id'])
        self.assertTrue(all(line['baseline'] is None for line in data['lines']))
        line = session.line_ids.filtered(lambda l: l.product_id == self.product)
        self.assertTrue(session.execute(str(uuid.uuid4()), {'op': 'set_count', 'line_id': line.id, 'quantity': 0})['ok'])
        self.assertTrue(session.execute(str(uuid.uuid4()), {'op': 'submit_review'})['ok'])
        self.finish(session)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.stock), 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.other, self.stock), 7)
        data = self.Session.start_count(self.stock.id, str(uuid.uuid4()))
        session = self.Session.browse(data['id'])
        self.assertTrue(self.scan(session, 'SCAN-B')['ok'])
        session.execute(str(uuid.uuid4()), {'op': 'submit_review'})
        self.env['stock.quant']._update_available_quantity(self.other, self.stock, 1)
        self.assertFalse(session.execute(str(uuid.uuid4()), {'op': 'finish'})['ok'])
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.other, self.stock), 8)

    def test_inventory_apply_requires_manager(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 10)
        model = self.Session.with_user(self.operator)
        session = model.browse(model.start_count(self.stock.id, str(uuid.uuid4()))['id'])
        self.assertTrue(self.scan(session)['ok'])
        session.execute(str(uuid.uuid4()), {'op': 'submit_review'})
        result = session.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertFalse(result['ok'])
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.stock), 10)

    def test_location_requirement(self):
        transfer = self.make_picking(qty=1)
        transfer.picking_type_id.qing_scan_destination_required = True
        self.stock.barcode = 'LOC-STOCK'
        session = self.start(transfer)
        self.assertFalse(self.scan(session)['ok'])
        self.assertTrue(self.scan(session, 'LOC-STOCK', kind='destination')['ok'])
        self.assertTrue(self.scan(session)['ok'])
        self.finish(session)
        self.assertEqual(transfer.state, 'done')

    def test_packaging_unit_and_gs1_receipt(self):
        carton = self.env['uom.uom'].create({'name': 'Carton of 6', 'relative_uom_id': self.product.uom_id.id, 'relative_factor': 6})
        self.env['product.uom'].create({'product_id': self.product.id, 'uom_id': carton.id, 'barcode': 'CARTON6'})
        session = self.start(self.make_picking())
        result = self.scan(session, 'CARTON6')
        self.assertTrue(result['ok'], result)
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 6)
        self.assertTrue(self.scan(session, 'CARTON6')['ok'])
        self.finish(session)
        self.assertEqual(session.picking_id.state, 'done')
        from odoo.tools.barcode import get_barcode_check_digit
        gtin = '09506000134350'
        gtin = gtin[:-1] + str(get_barcode_check_digit(gtin))
        self.lot_product.barcode = gtin
        self.env.company.nomenclature_id = self.env.ref('barcodes_gs1_nomenclature.default_gs1_nomenclature')
        self.other.default_code = self.lot_product.barcode
        session = self.start(self.make_picking(self.lot_product, qty=3))
        result = self.scan(session, '01' + gtin + '10BATCH-GS1\x1d303')
        self.assertTrue(result['ok'], result)
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 3)
        self.finish(session)
        self.assertEqual(session.picking_id.move_line_ids.lot_id.name, 'BATCH-GS1')

    def test_whole_package_preview_duplicate_and_undo(self):
        package = self.env['stock.package'].create({'name': 'BOX-ONE', 'company_id': self.env.company.id})
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 12, package_id=package)
        transfer = self.make_picking(direction='outgoing')
        session = self.start(transfer)
        preview = self.scan(session, 'BOX-ONE', kind='package')
        self.assertTrue(preview['ok'], preview)
        self.assertTrue(preview['result']['package_preview'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 0)
        result = self.scan(session, 'BOX-ONE', kind='package', confirm_package=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 12)
        self.assertFalse(self.scan(session, 'BOX-ONE', kind='package', confirm_package=True)['ok'])
        self.assertFalse(self.scan(session)['ok'])
        self.assertTrue(session.execute(str(uuid.uuid4()), {'op': 'undo'})['ok'])
        self.assertEqual(sum(session.line_ids.mapped('quantity')), 0)
        self.assertTrue(self.scan(session, 'BOX-ONE', kind='package', confirm_package=True)['ok'])
        self.finish(session)
        self.assertEqual(transfer.move_line_ids.result_package_id, package)

    def test_return_request_is_idempotent(self):
        transfer = self.make_picking(qty=1)
        session = self.start(transfer)
        self.scan(session)
        finish_key = str(uuid.uuid4())
        first = session.execute(finish_key, {'op': 'finish'})
        self.assertTrue(first['ok'], first)
        self.assertEqual(transfer.state, 'done')
        self.assertFalse(session.execute(finish_key, {'op': 'finish'})['result']['action'])
        key = str(uuid.uuid4())
        first_return = session.execute(key, {'op': 'return'})
        self.assertTrue(first_return['ok'], first_return)
        result = session.execute(key, {'op': 'return'})
        self.assertEqual(result['result']['action']['res_id'], first_return['result']['action']['res_id'])
        returned = self.env['stock.picking'].browse(result['result']['action']['res_id'])
        returned.action_return_all()
        returned.action_confirm()
        returned.action_assign()
        return_session = self.start(returned)
        result = self.scan(return_session)
        self.assertTrue(result['ok'], result)
        self.finish(return_session)
        self.assertEqual(returned.state, 'done')

    def test_internal_transfer_and_lookup(self):
        destination = self.env['stock.location'].create({'name': 'Shelf B', 'usage': 'internal',
                                                       'location_id': self.stock.id, 'barcode': 'SHELF-B'})
        self.stock.barcode = 'SHELF-A'
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 2)
        transfer = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.int_type_id.id, 'location_id': self.stock.id,
            'location_dest_id': destination.id,
            'move_ids': [Command.create({'product_id': self.product.id, 'product_uom_qty': 2,
                                        'uom_id': self.product.uom_id.id, 'location_id': self.stock.id,
                                        'location_dest_id': destination.id})],
        })
        transfer.action_confirm()
        transfer.action_assign()
        session = self.start(transfer)
        self.assertTrue(self.scan(session, 'SHELF-A', kind='source')['ok'])
        self.assertTrue(self.scan(session, 'SHELF-B', kind='destination')['ok'])
        self.assertTrue(self.scan(session, quantity=2)['ok'])
        self.finish(session)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, destination), 2)
        result = self.Session.lookup_stock('SHELF-B')
        self.assertEqual(sum(r['quantity'] for r in result['rows']), 2)

    def test_company_scope_and_manager_takeover(self):
        transfer = self.make_picking()
        session = self.start(transfer, self.operator)
        other_company = self.env['res.company'].create({'name': 'Other Scan Company'})
        with self.assertRaises(AccessError):
            session.with_company(other_company).get_session()
        manager_session = self.Session.browse(session.id)
        result = manager_session.execute(str(uuid.uuid4()), {'op': 'take_over'})
        self.assertTrue(result['ok'], result)
        with self.assertRaises(AccessError):
            self.scan(session)
        self.assertTrue(self.scan(manager_session)['ok'])

    def test_full_receipt_snapshot_includes_recomputed_move_timestamp(self):
        transfer = self.make_picking(qty=1)
        transfer.move_ids.flush_recordset()
        # Model an earlier HTTP transaction; TransactionCase otherwise shares one timestamp.
        self.env.cr.execute("UPDATE stock_move SET write_date = '2000-01-01' WHERE id = %s", [transfer.move_ids.id])
        transfer.move_ids.invalidate_recordset(['write_date'])
        session = self.start(transfer, self.operator)
        self.assertTrue(self.scan(session)['ok'])
        result = session.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(transfer.state, 'done')
        self.assertEqual(session.state, 'done')
        self.assertFalse(transfer.backorder_ids)

    def test_validation_matches_pc_backorder_policy(self):
        for user in (self.operator, self.env.user):
            for policy, create, scanned in (
                ('ask', True, 1), ('ask', False, 1), ('always', True, 1),
                ('never', False, 1), ('ask', False, 2),
            ):
                with self.subTest(user=user.login, policy=policy, create=create, scanned=scanned):
                    self.warehouse.in_type_id.create_backorder = policy
                    pc = self.make_picking(qty=2).with_user(user)
                    mobile = self.make_picking(qty=2)
                    session = self.start(mobile, user)
                    pc.move_line_ids.write({'quantity': scanned, 'picked': True})
                    pc_action = pc.button_validate()
                    self.assertTrue(session.get_session()['validation_blocker'])
                    self.assertTrue(self.scan(session, quantity=scanned)['ok'])
                    self.assertFalse(session.get_session()['validation_blocker'])
                    key = str(uuid.uuid4())
                    result = session.execute(key, {'op': 'finish'})
                    self.assertTrue(result['ok'], result)
                    action = result['result']['action']
                    if policy == 'ask' and scanned < 2:
                        self.assertEqual(action['res_model'], 'stock.backorder.confirmation')
                        self.assertEqual(pc_action['res_model'], action['res_model'])
                        self.assertEqual(session.state, 'applied')
                        self.assertFalse(session.get_session()['validation_blocker'])
                        # Closing the wizard and validating again must not add quantities again.
                        retry = session.execute(str(uuid.uuid4()), {'op': 'finish'})
                        self.assertTrue(retry['ok'], retry)
                        self.assertEqual(sum(mobile.move_line_ids.mapped('quantity')), scanned)
                        for native_action in (pc_action, retry['result']['action']):
                            wizard = self.env['stock.backorder.confirmation'].with_user(user).with_context(
                                native_action['context']).create({})
                            (wizard.process if create else wizard.process_cancel_backorder)()
                    else:
                        self.assertFalse(isinstance(action, dict) and action.get('res_model') == 'stock.backorder.confirmation')
                    self.assertEqual(pc.state, 'done')
                    self.assertEqual(mobile.state, pc.state)
                    self.assertEqual(session.state, 'done')
                    self.assertTrue(session.get_session()['validation_blocker'])
                    self.assertEqual(sum(mobile.move_line_ids.mapped('quantity')), scanned)
                    self.assertEqual(sum(pc.move_line_ids.mapped('quantity')), scanned)
                    expected = 2 - scanned if create else 0
                    self.assertEqual(sum(mobile.backorder_ids.move_ids.mapped('product_uom_qty')), expected)
                    self.assertEqual(sum(pc.backorder_ids.move_ids.mapped('product_uom_qty')), expected)
                    self.assertFalse(session.execute(key, {'op': 'finish'})['result']['action'])
                    self.assertEqual(len(mobile.backorder_ids), int(expected > 0))

    def test_validate_availability_requires_locations_and_complete_lot(self):
        operation = self.warehouse.in_type_id
        self.supplier = self.supplier.copy({'company_id': self.env.company.id, 'barcode': 'SCAN-SOURCE'})
        self.stock.barcode = 'SCAN-TARGET'
        session = self.start(self.make_picking(product=self.lot_product, qty=2), self.operator)
        self.assertTrue(session.get_session()['validation_blocker'])
        self.assertFalse(session.execute(str(uuid.uuid4()), {'op': 'finish'})['ok'])
        self.scan(session, 'SCAN-L')
        self.assertIn('pending', session.get_session()['validation_blocker'])
        self.assertTrue(self.scan(session, 'LOT-READY')['ok'])
        operation.write({'qing_scan_source_required': True, 'qing_scan_destination_required': True})
        self.assertIn('source location', session.get_session()['validation_blocker'])
        self.assertTrue(self.scan(session, 'SCAN-SOURCE', kind='source')['ok'])
        self.assertIn('destination location', session.get_session()['validation_blocker'])
        self.assertTrue(self.scan(session, 'SCAN-TARGET', kind='destination')['ok'])
        self.assertFalse(session.get_session()['validation_blocker'])
        self.assertTrue(session.with_user(self.reviewer).get_session()['validation_blocker'])
        self.finish(session)
        self.assertEqual(session.picking_id.state, 'done')

    def test_serial_already_in_stock_cannot_be_received_again(self):
        lot = self.env['stock.lot'].create({'name': 'SER-EXISTING', 'product_id': self.serial.id})
        self.env['stock.quant']._update_available_quantity(self.serial, self.stock, 1, lot_id=lot)
        self.warehouse.in_type_id.use_existing_lots = True
        session = self.start(self.make_picking(self.serial, qty=1))
        self.scan(session, 'SCAN-S')
        self.assertTrue(self.scan(session, 'SER-EXISTING')['ok'])
        result = session.execute(str(uuid.uuid4()), {'op': 'finish'})
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.serial, self.stock, lot_id=lot), 1)

    def test_scan_entry_routes_without_claiming_and_checks_permissions(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.stock, 10)
        for direction in ('incoming', 'outgoing', 'internal'):
            transfer = self.make_picking(qty=1)
            if direction == 'outgoing':
                transfer.picking_type_id = self.warehouse.out_type_id
            elif direction == 'internal':
                transfer.picking_type_id = self.warehouse.int_type_id
            count = self.Session.search_count([])
            action = transfer.with_user(self.operator).action_qing_scan()
            self.assertEqual(action['params'], {'picking_id': transfer.id, 'operation': direction, 'picking_state': transfer.state})
            self.assertEqual(self.Session.search_count([]), count)
            transfer.move_ids.quantity = 1
            transfer.move_ids.picked = True
            transfer.button_validate()
            self.assertEqual(transfer.with_user(self.second).action_qing_scan()['params']['picking_state'], 'done')
        with self.assertRaises(AccessError):
            transfer.with_user(self.env.ref('base.public_user')).action_qing_scan()
        other_company = self.env['res.company'].create({'name': 'Other navigation company'})
        with self.assertRaises(AccessError):
            transfer.with_company(other_company).action_qing_scan()
        cancelled = self.make_picking()
        cancelled.action_cancel()
        with self.assertRaises(UserError):
            cancelled.with_user(self.operator).action_qing_scan()
