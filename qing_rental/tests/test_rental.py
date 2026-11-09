import logging
from datetime import timedelta
from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged


_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestRental(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [
            Command.link(cls.env.ref('qing_rental.group_rental_manager').id),
            Command.link(cls.env.ref('stock.group_stock_manager').id),
            Command.link(cls.env.ref('sales_team.group_sale_manager').id),
        ]})
        cls.warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.env.company.id),
        ], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Rental test warehouse', 'code': 'QRT',
                'company_id': cls.env.company.id,
            })
        cls.warehouse.action_qing_setup_rental()
        cls.rental_service = cls._create_product(
            name='Rental fee', type='service', invoice_policy='order',
            uom_id=cls.uom_unit.id, taxes_id=[Command.clear()],
        )
        cls.equipment = cls._create_product(
            name='Rental equipment', type='consu', is_storable=True,
            uom_id=cls.uom_unit.id, qing_rental_ok=True,
            qing_rental_service_id=cls.rental_service.id,
            qing_rental_period='day', qing_rental_rate=10,
            taxes_id=[Command.clear()],
        )
        cls.env['stock.quant']._update_available_quantity(
            cls.equipment, cls.warehouse.qing_rental_stock_location_id, 10,
        )
        cls.start = fields.Datetime.now().replace(microsecond=0) + timedelta(days=30)
        cls.end = cls.start + timedelta(days=2)
        cls.rental_user = new_test_user(
            cls.env, login='qing_rental_operator',
            groups='qing_rental.group_rental_user,sales_team.group_sale_salesman_all_leads,base.group_partner_manager',
            company_id=cls.env.company.id,
        )
        cls.sales_user = new_test_user(
            cls.env, login='qing_rental_non_operator',
            groups='sales_team.group_sale_salesman_all_leads,base.group_partner_manager',
            company_id=cls.env.company.id,
        )
        cls.sales_team = cls.env['crm.team'].create({
            'name': 'Rental test sales team',
            'member_ids': [Command.set((cls.rental_user | cls.sales_user).ids)],
        })
        cls.deposit_account = cls.company_data['default_account_revenue'].copy({
            'name': 'Refundable rental deposits', 'code': 'QRENTDEP',
            'account_type': 'liability_current', 'reconcile': True,
        })
        cls.deposit_journal = cls.company_data['default_journal_bank']

    def _order(self, quantity=1, start=None, end=None, line_values=None, **values):
        line = {'product_id': self.equipment.id, 'quantity': quantity}
        line.update(line_values or {})
        return self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'warehouse_id': self.warehouse.id,
            'company_id': self.env.company.id,
            'team_id': self.sales_team.id,
            'is_qing_rental': True,
            'qing_rental_start': start or self.start,
            'qing_rental_end': end or self.end,
            'qing_rental_line_ids': [Command.create(line)],
            **values,
        })

    def _renew(self, order, new_end):
        renewal = self.env['qing.rental.renew'].create({
            'order_id': order.id, 'new_end': new_end,
        })
        return renewal.action_apply()

    def _pickup(self, order, quantity=None):
        with patch.object(fields.Datetime, 'now', return_value=order.qing_rental_start):
            action = order.action_qing_rental_pickup()
            picking = self.env['stock.picking'].browse(action['res_id'])
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty if quantity is None else quantity
                move.picked = True
            picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        return picking

    def _deposit(self, order, amount=100, kind='receipt', **values):
        return self.env['qing.rental.deposit'].create({
            'order_id': order.id, 'amount': amount, 'kind': kind,
            'journal_id': self.deposit_journal.id,
            'liability_account_id': self.deposit_account.id,
            'reference': 'RENTAL-%s-%s' % (order.id, kind),
            **values,
        })

    def test_renewal_keeps_the_agreed_quotation_discount(self):
        order = self._order(quantity=2)
        order.action_qing_rental_reprice()
        original = order.order_line
        original.discount = 10
        order.action_confirm()
        self.assertEqual(original.price_subtotal, 36)
        wizard = self.env['qing.rental.renew'].create({'order_id': order.id, 'new_end': self.end + timedelta(days=1)})
        self.assertEqual(wizard.charge_amount, 18)
        wizard.action_apply()
        renewal = order.order_line.filtered(lambda line: line.qing_rental_charge == 'renewal')
        self.assertEqual(renewal.price_subtotal, 18)
        self.assertEqual(original.price_subtotal, 36)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            original.discount = 50

    def test_quote_defaults_and_day_rounding(self):
        order = self._order(quantity=3, end=self.start + timedelta(days=1, seconds=1))
        line = order.qing_rental_line_ids
        self.assertEqual(line.service_product_id, self.rental_service)
        self.assertEqual(line.rate, 10)
        self.assertEqual(line.period, 'day')
        self.assertEqual(line.amount, 60)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.amount_untaxed, 60)
        self.assertEqual(order.order_line.product_id, self.rental_service)
        self.assertEqual(order.order_line.qing_rental_line_id, line)
        self.assertFalse(order.picking_ids, 'Rental fees must not trigger ordinary sales delivery.')

    def test_week_and_thirty_day_month_rounding(self):
        cases = [('week', 7, 1), ('week', 8, 2), ('month', 30, 1), ('month', 31, 2)]
        for period, days, charged_periods in cases:
            with self.subTest(period=period, days=days):
                order = self._order(
                    quantity=2, end=self.start + timedelta(days=days),
                    line_values={'period': period, 'rate': 15},
                )
                self.assertEqual(order.qing_rental_line_ids.amount, 30 * charged_periods)

    def test_confirm_rejects_overbooking(self):
        reserved = self._order(quantity=6)
        reserved.action_confirm()
        candidate = self._order(quantity=5)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            candidate.action_confirm()
        self.assertEqual(candidate.state, 'draft')
        self.assertEqual(reserved.state, 'sale')

    def test_quantity_exactly_equal_to_remaining_capacity(self):
        self._order(quantity=6).action_confirm()
        order = self._order(quantity=4)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_non_overlapping_bookings_reuse_capacity(self):
        self._order(quantity=10).action_confirm()
        order = self._order(quantity=10, start=self.end + timedelta(days=1), end=self.end + timedelta(days=2))
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_adjacent_half_open_periods_reuse_capacity(self):
        self._order(quantity=10).action_confirm()
        order = self._order(quantity=10, start=self.end, end=self.end + timedelta(days=1))
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_capacity_uses_peak_occupancy_not_sum_of_disjoint_bookings(self):
        middle = self.start + timedelta(days=1)
        self._order(quantity=4, end=middle).action_confirm()
        self._order(quantity=4, start=middle).action_confirm()
        spanning = self._order(quantity=6)
        spanning.action_confirm()
        self.assertEqual(spanning.state, 'sale')

    def test_turnaround_time_blocks_immediate_next_booking(self):
        self._order(quantity=10, line_values={'padding_hours': 2}).action_confirm()
        too_early = self._order(quantity=1, start=self.end, end=self.end + timedelta(days=1))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            too_early.action_confirm()
        ready = self._order(quantity=10, start=self.end + timedelta(hours=2), end=self.end + timedelta(days=1))
        ready.action_confirm()
        self.assertEqual(ready.state, 'sale')

    def test_overdue_outstanding_equipment_blocks_future_bookings(self):
        order = self._order(quantity=10)
        order.action_confirm()
        self._pickup(order)
        with patch.object(fields.Datetime, 'now', return_value=self.end + timedelta(hours=1)):
            candidate = self._order(quantity=1, start=self.end + timedelta(days=2), end=self.end + timedelta(days=3))
            with self.assertRaises(UserError), self.env.cr.savepoint():
                candidate.action_confirm()

    def test_partial_pickup_overdue_only_blocks_actual_borrowed_units(self):
        order = self._order(quantity=3)
        order.action_confirm()
        self._pickup(order, quantity=1)
        self.assertEqual(order.qing_rental_line_ids.qty_out, 1)
        with patch.object(fields.Datetime, 'now', return_value=self.end + timedelta(hours=1)):
            candidate = self._order(quantity=9, start=self.end + timedelta(days=2), end=self.end + timedelta(days=3))
            self.assertEqual(candidate._qing_rental_available()[self.equipment.id], 9)
            candidate.action_confirm()
            self.assertEqual(candidate.state, 'sale')

    def test_duplicate_equipment_lines_are_aggregated(self):
        order = self._order(quantity=6)
        order.write({'qing_rental_line_ids': [Command.create({
            'product_id': self.equipment.id, 'quantity': 5,
        })]})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.action_confirm()

    def test_availability_indicator_updates_quantity_and_duplicate_lines(self):
        order = self._order(quantity=10)
        line = order.qing_rental_line_ids
        self.assertEqual(line.availability_state, 'available')
        line.quantity = 11
        self.assertEqual(line.availability_state, 'unavailable')
        line.quantity = 6
        self.assertEqual(line.availability_state, 'available')
        order.write({'qing_rental_line_ids': [Command.create({
            'product_id': self.equipment.id, 'quantity': 5,
        })]})
        self.assertEqual(order.qing_rental_line_ids.mapped('required_qty'), [11, 11])
        self.assertEqual(order.qing_rental_line_ids.mapped('availability_state'), ['unavailable'] * 2)
        order.qing_rental_line_ids[-1].unlink()
        self.assertEqual(line.availability_state, 'available')

    def test_availability_indicator_unsaved_order_and_period_changes(self):
        reserved = self._order(quantity=6)
        reserved.action_confirm()
        order = self.env['sale.order'].new({
            'partner_id': self.partner_a.id, 'warehouse_id': self.warehouse.id,
            'company_id': self.env.company.id, 'is_qing_rental': True,
            'qing_rental_start': self.start, 'qing_rental_end': self.end,
            'qing_rental_line_ids': [Command.create({'product_id': self.equipment.id, 'quantity': 5})],
        })
        line = order.qing_rental_line_ids
        self.assertEqual(line.available_qty, 4)
        self.assertEqual(line.availability_state, 'unavailable')
        line.quantity = 4
        self.assertEqual(line.availability_state, 'available')
        line.quantity = 5
        order.update({'qing_rental_start': self.end, 'qing_rental_end': self.end + timedelta(days=2)})
        self.assertEqual(line.available_qty, 10)
        self.assertEqual(line.availability_state, 'available')
        order.qing_rental_end = False
        self.assertFalse(line.availability_state)

    def test_availability_indicator_excludes_own_confirmed_reservation(self):
        order = self._order(quantity=10)
        order.action_confirm()
        self.assertEqual(order.qing_rental_line_ids.availability_state, 'available')
        draft = self.env['sale.order'].new({}, origin=order)
        self.assertEqual(draft.qing_rental_line_ids.available_qty, 10)
        self.assertEqual(draft.qing_rental_line_ids.availability_state, 'available')
        order.action_cancel()
        self.assertFalse(order.qing_rental_line_ids.availability_state)

    def test_availability_indicator_batches_fifty_and_one_hundred_fifty_lines(self):
        counts = []
        for size in (50, 150):
            with self.env.cr.savepoint() as checkpoint:
                order = self._order(qing_rental_line_ids=[Command.create({
                    'product_id': self.equipment.id, 'quantity': 1,
                }) for _ in range(size)])
                lines = order.qing_rental_line_ids
                lines.read(['available_qty', 'required_qty', 'availability_state'])
                self.env.flush_all()
                before = self.env.cr.sql_log_count
                lines[0].quantity = 2
                results = lines.read(['available_qty', 'required_qty', 'availability_state'])
                self.env.flush_all()
                counts.append(self.env.cr.sql_log_count - before)
                self.assertTrue(all(row['required_qty'] == size + 1 for row in results))
                self.assertTrue(all(row['availability_state'] == 'unavailable' for row in results))
                self.assertEqual(lines[1:].mapped('quantity'), [1] * (size - 1))
                checkpoint.close()
        _logger.info('Rental availability edit/read SQL for 50/150 lines: %s', counts)
        self.assertLessEqual(counts[1], counts[0] + 10, 'Availability queries must not grow per equipment line.')

    def test_cancellation_releases_future_reservation(self):
        first = self._order(quantity=10)
        first.action_confirm()
        first.action_cancel()
        self.assertEqual(first.state, 'cancel')
        second = self._order(quantity=10)
        second.action_confirm()
        self.assertEqual(second.state, 'sale')

    def test_draft_quotation_does_not_reserve_capacity(self):
        self._order(quantity=10)
        order = self._order(quantity=10)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_ordinary_stock_is_not_rental_capacity(self):
        self.env['stock.quant']._update_available_quantity(self.equipment, self.warehouse.lot_stock_id, 100)
        order = self._order(quantity=11)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.action_confirm()

    def test_quantities_must_be_positive_whole_units(self):
        for quantity in (0, -1, 1.5):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._order(quantity=quantity)

    def test_invalid_period_and_negative_rates_are_rejected(self):
        for end in (self.start, self.start - timedelta(seconds=1)):
            with self.subTest(end=end), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._order(end=end)
        for values in ({'rate': -1}, {'padding_hours': -1}):
            with self.subTest(values=values), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._order(line_values=values)

    def test_non_rentable_goods_are_rejected(self):
        product = self.equipment.copy({'qing_rental_ok': False})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._order(line_values={'product_id': product.id})

    def test_confirmed_terms_cannot_be_rewritten_or_deleted(self):
        order = self._order()
        order.action_confirm()
        for record, values in (
            (order, {'qing_rental_end': self.end + timedelta(days=1)}),
            (order.qing_rental_line_ids, {'quantity': 2}),
            (order.qing_rental_line_ids, {'rate': 1}),
        ):
            with self.subTest(values=values), self.assertRaises(UserError), self.env.cr.savepoint():
                record.write(values)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.qing_rental_line_ids.unlink()

    def test_confirmed_rental_cannot_be_reset_by_writing_sent_state(self):
        order = self._order()
        order.action_confirm()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.write({'state': 'sent'})
        self.assertEqual(order.state, 'sale')

    def test_default_order_id_cannot_add_equipment_to_confirmed_rental(self):
        order = self._order()
        order.action_confirm()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.env['qing.rental.line'].with_context(default_order_id=order.id).create({
                'product_id': self.equipment.id, 'quantity': 1,
            })
        self.assertEqual(len(order.qing_rental_line_ids), 1)

    def test_rpc_cannot_forge_computed_equipment_quantities_or_totals(self):
        order = self._order()
        line = order.qing_rental_line_ids
        for values in ({'qty_returned': 100}, {'qty_out': 100}, {'available_qty': 100}, {'amount': 1}):
            with self.subTest(values=values), self.assertRaises(UserError), self.env.cr.savepoint():
                line.write(values)
            with self.subTest(create_values=values), self.assertRaises(UserError), self.env.cr.savepoint():
                self.env['qing.rental.line'].create({
                    'order_id': order.id, 'product_id': self.equipment.id, 'quantity': 1, **values,
                })
            context = {'default_' + key: value for key, value in values.items()}
            with self.subTest(context=context), self.assertRaises(UserError), self.env.cr.savepoint():
                self.env['qing.rental.line'].with_context(**context).create({
                    'order_id': order.id, 'product_id': self.equipment.id, 'quantity': 1,
                })
        self.assertEqual(line.qty_returned, 0)
        self.assertEqual(line.qty_out, 0)
        self.assertEqual(line.amount, 20)

    def test_duplicate_order_regenerates_rental_fees_once(self):
        order = self._order(quantity=2)
        order.action_confirm()
        duplicate = order.copy()
        self.assertEqual(duplicate.state, 'draft')
        self.assertEqual(len(duplicate.qing_rental_line_ids), 1)
        self.assertFalse(duplicate.order_line.filtered('qing_rental_line_id'))
        duplicate.action_confirm()
        self.assertEqual(len(duplicate.order_line), 1)
        self.assertEqual(duplicate.order_line.qing_rental_line_id, duplicate.qing_rental_line_ids)
        self.assertEqual(duplicate.amount_untaxed, 40)

    def test_rpc_context_cannot_impersonate_private_workflow(self):
        order = self._order()
        order.action_confirm()
        for token in (True, 'INTERNAL_TOKEN', {'authorized': True}):
            with self.subTest(token=token), self.assertRaises(UserError), self.env.cr.savepoint():
                order.qing_rental_line_ids.with_context(_qing_rental_internal=token).write({'quantity': 100})

    def test_rpc_cannot_create_confirmed_rental_or_forge_billing_links(self):
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._order(state='sale')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.env['sale.order'].with_context(default_state='sale').create({
                'partner_id': self.partner_a.id, 'warehouse_id': self.warehouse.id,
                'is_qing_rental': True, 'qing_rental_start': self.start, 'qing_rental_end': self.end,
            })
        order = self._order()
        line = order.qing_rental_line_ids
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.env['sale.order.line'].with_context(
                default_qing_rental_line_id=line.id, default_qing_rental_charge='rent',
            ).create({'order_id': order.id, 'product_id': self.rental_service.id, 'product_uom_qty': 1})

    def test_renewal_appends_delta_without_changing_invoiced_charge(self):
        order = self._order(quantity=2)
        order.action_confirm()
        original = order.order_line
        invoice = order._create_invoices()
        original_values = original.read(['price_unit', 'product_uom_qty', 'name'])[0]
        self.assertEqual(invoice.amount_untaxed, 40)
        self._renew(order, self.end + timedelta(days=1))
        self.assertEqual(order.qing_rental_end, self.end + timedelta(days=1))
        self.assertEqual(original.read(['price_unit', 'product_uom_qty', 'name'])[0], original_values)
        self.assertEqual(invoice.amount_untaxed, 40)
        self.assertEqual(order.amount_untaxed, 60)
        self.assertEqual(len(order.order_line - original), 1)
        delta_invoice = order._create_invoices()
        self.assertEqual(delta_invoice.amount_untaxed, 20)

    def test_payment_status_tracks_native_rent_receipts_and_unreconcile(self):
        order = self._order()
        order.action_confirm()
        self.assertEqual(order.qing_payment_status, 'not_paid')
        invoice = order._create_invoices()
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        self.assertEqual(order.qing_payment_status, 'not_paid', 'Invoicing is not payment.')
        first = self._register_payment(
            invoice, amount=8, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.inbound_payment_method_line.id,
        )
        self.assertEqual(order.qing_payment_status, 'partial')
        self.assertEqual(order.qing_received_amount, 8)
        self._register_payment(
            invoice, amount=12, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.inbound_payment_method_line.id,
        )
        self.assertEqual(order.qing_payment_status, 'paid')
        self.assertEqual(order.qing_received_amount, 20)
        first.move_id.line_ids.remove_move_reconcile()
        self.assertEqual(order.qing_payment_status, 'partial')
        self.assertEqual(order.qing_received_amount, 12)

    def test_payment_status_ignores_deposit_receipts_and_refunds(self):
        order = self._order()
        order.action_confirm()
        self._deposit(order, amount=100).action_post()
        self.assertEqual(order.qing_payment_status, 'not_paid')
        self.assertEqual(order.qing_received_amount, 0)
        invoice = order._create_invoices()
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        self._register_payment(
            invoice, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.inbound_payment_method_line.id,
        )
        self._deposit(order, amount=40, kind='refund').action_post()
        self.assertEqual(order.qing_rental_deposit_balance, 60)
        self.assertEqual(order.qing_payment_status, 'paid')
        self.assertEqual(order.qing_received_amount, 20)
        self.assertEqual(order.qing_refunded_amount, 0)

    def test_payment_status_requires_payment_for_renewal_charges(self):
        order = self._order()
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        self._register_payment(
            invoice, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.inbound_payment_method_line.id,
        )
        self.assertEqual(order.qing_payment_status, 'paid')
        self._renew(order, self.end + timedelta(days=1))
        self.assertEqual(order.amount_total, 30)
        self.assertEqual(order.qing_payment_status, 'partial')
        self.assertEqual(order.qing_received_amount, 20)
        renewal_invoice = order._create_invoices()
        renewal_invoice.invoice_date = fields.Date.today()
        renewal_invoice.action_post()
        self._register_payment(
            renewal_invoice, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.inbound_payment_method_line.id,
        )
        self.assertEqual(order.qing_payment_status, 'paid')
        self.assertEqual(order.qing_received_amount, 30)

    def test_payment_status_tracks_rent_refunds(self):
        order = self._order()
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        self._register_payment(
            invoice, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.inbound_payment_method_line.id,
        )
        refund = self._reverse_invoice(invoice, post=True)
        self.assertEqual(order.qing_payment_status, 'paid', 'A credit note is not a cash refund.')
        self._register_payment(
            refund, amount=8, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.outbound_payment_method_line.id,
        )
        self.assertEqual(order.qing_payment_status, 'partial_refund')
        self.assertEqual(order.qing_refunded_amount, 8)
        self._register_payment(
            refund, amount=12, journal_id=self.deposit_journal.id,
            payment_method_line_id=self.outbound_payment_method_line.id,
        )
        self.assertEqual(order.qing_payment_status, 'refunded')
        self.assertEqual(order.qing_refunded_amount, 20)

    def test_rental_operator_can_read_and_search_payment_status(self):
        order = self._order()
        order.action_confirm()
        self.assertFalse(self.rental_user.has_group('account.group_account_invoice'))
        values = order.with_user(self.rental_user).read([
            'qing_payment_status', 'qing_received_amount', 'qing_refunded_amount',
        ])[0]
        self.assertEqual(values['qing_payment_status'], 'not_paid')
        self.assertEqual(values['qing_received_amount'], 0)
        self.assertEqual(values['qing_refunded_amount'], 0)
        self.assertEqual(self.env['sale.order'].with_user(self.rental_user).search([
            ('id', '=', order.id), ('qing_payment_status', '=', 'not_paid'),
        ]), order.with_user(self.rental_user))

    def test_rental_payment_views_reuse_sales_fields_without_duplicates(self):
        order_model = self.env['sale.order'].with_user(self.rental_user)
        arch = order_model.get_view(
            self.env.ref('qing_rental.view_sale_order_list').id, 'list',
        )['arch']
        fields = etree.fromstring(arch).xpath("./field[@name='qing_payment_status']")
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].get('optional'), 'show')
        self.assertEqual(fields[0].get('widget'), 'badge')
        search = etree.fromstring(order_model.get_view(
            self.env.ref('qing_rental.view_sale_order_search').id, 'search',
        )['arch'])
        self.assertEqual(len(search.xpath("./field[@name='qing_payment_status']")), 1)
        for name, status in (
            ('qing_unpaid', 'not_paid'), ('qing_partially_paid', 'partial'),
            ('qing_paid', 'paid'), ('qing_payment_review', 'review'),
        ):
            node = search.xpath(f"./filter[@name='{name}']")[0]
            self.assertEqual(node.get('domain'), f"[('qing_payment_status', '=', '{status}')]")
        self.assertEqual(search.xpath(
            "./group/filter[@name='qing_payment_status_group']",
        )[0].get('context'), "{'group_by': 'qing_payment_status'}")
        form = etree.fromstring(order_model.get_view(
            self.env.ref('sale.view_order_form').id, 'form',
        )['arch'])
        for name in ('qing_payment_status', 'qing_received_amount', 'qing_refunded_amount'):
            self.assertEqual(len(form.xpath(f".//field[@name='{name}']")), 1)
        action = self.env.ref('qing_rental.action_rental_orders')
        self.assertEqual(action.domain, "[('is_qing_rental', '=', True)]")
        self.assertEqual(action.context, "{'default_is_qing_rental': True}")

    def test_renewal_checks_overlapping_reservations(self):
        first = self._order(quantity=10)
        first.action_confirm()
        self._order(quantity=10, start=self.end, end=self.end + timedelta(days=2)).action_confirm()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._renew(first, self.end + timedelta(days=1))
        self.assertEqual(first.qing_rental_end, self.end)
        self.assertEqual(len(first.order_line), 1)

    def test_renewal_requires_a_later_end(self):
        order = self._order()
        order.action_confirm()
        for end in (self.end, self.end - timedelta(days=1)):
            with self.subTest(end=end), self.assertRaises((UserError, ValidationError)), self.env.cr.savepoint():
                self._renew(order, end)

    def test_sales_user_without_rental_role_cannot_confirm(self):
        order = self._order().with_user(self.sales_user)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.action_confirm()
        self.assertEqual(order.with_env(self.env).state, 'draft')

    def test_rental_operator_can_confirm_without_inventory_write_access(self):
        order = self._order().with_user(self.rental_user)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertFalse(self.rental_user.has_group('stock.group_stock_user'))

    def test_cross_company_order_is_not_accessible(self):
        order = self._order()
        other_company = self.env['res.company'].create({'name': 'Rental inaccessible company'})
        self.rental_user.write({
            'company_id': other_company.id,
            'company_ids': [Command.set(other_company.ids)],
        })
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.with_user(self.rental_user).with_context(allowed_company_ids=other_company.ids).action_confirm()

    def test_deposit_receipt_and_refund_post_balanced_native_entries(self):
        order = self._order()
        order.action_confirm()
        receipt = self._deposit(order, amount=100)
        receipt.action_post()
        self.assertEqual(receipt.state, 'posted')
        self.assertEqual(receipt.move_id.state, 'posted')
        self.assertEqual(receipt.move_id.qing_rental_deposit_id, receipt)
        self.assertEqual(order.qing_rental_deposit_balance, 100)
        lines = receipt.move_id.line_ids
        self.assertEqual(sum(lines.mapped('balance')), 0)
        self.assertEqual(lines.filtered(lambda line: line.account_id == self.deposit_account).credit, 100)
        self.assertEqual(lines.filtered(lambda line: line.account_id == self.deposit_journal.default_account_id).debit, 100)
        self.assertEqual(lines.partner_id, self.partner_a.commercial_partner_id)
        refund = self._deposit(order, amount=40, kind='refund')
        refund.action_post()
        self.assertEqual(refund.move_id.state, 'posted')
        self.assertEqual(order.qing_rental_deposit_balance, 60)
        refund_lines = refund.move_id.line_ids
        self.assertEqual(sum(refund_lines.mapped('balance')), 0)
        self.assertEqual(refund_lines.filtered(lambda line: line.account_id == self.deposit_account).debit, 40)
        self.assertEqual(refund_lines.filtered(lambda line: line.account_id == self.deposit_journal.default_account_id).credit, 40)
        self.assertEqual(order.amount_untaxed, 20, 'Deposits must not become rental revenue.')

    def test_deposit_post_is_idempotent_and_duplicate_reference_is_rejected(self):
        order = self._order()
        order.action_confirm()
        receipt = self._deposit(order)
        receipt.action_post()
        move = receipt.move_id
        receipt.action_post()
        self.assertEqual(receipt.move_id, move)
        self.assertEqual(order.qing_rental_deposit_balance, 100)
        duplicate = self._deposit(order)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            duplicate.action_post()
        self.assertEqual(duplicate.state, 'draft')
        self.assertFalse(duplicate.move_id)

    def test_deposit_refund_cannot_exceed_balance(self):
        order = self._order()
        order.action_confirm()
        self._deposit(order).action_post()
        excessive = self._deposit(order, amount=101, kind='refund')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            excessive.action_post()
        self.assertEqual(order.qing_rental_deposit_balance, 100)
        self.assertFalse(excessive.move_id)
        full_refund = self._deposit(order, amount=100, kind='refund', reference='REFUND-FULL')
        full_refund.action_post()
        self.assertEqual(order.qing_rental_deposit_balance, 0)

    def test_posted_deposit_cannot_be_rewritten_deleted_reset_or_reversed(self):
        order = self._order()
        order.action_confirm()
        receipt = self._deposit(order)
        receipt.action_post()
        for record, values in ((receipt, {'amount': 1}), (receipt, {'state': 'draft'})):
            with self.subTest(values=values), self.assertRaises(UserError), self.env.cr.savepoint():
                record.write(values)
        for action in (receipt.unlink, receipt.move_id.button_draft, receipt.move_id._reverse_moves):
            with self.subTest(action=action.__name__), self.assertRaises(UserError), self.env.cr.savepoint():
                action()
        self.assertEqual(receipt.move_id.state, 'posted')
        self.assertEqual(order.qing_rental_deposit_balance, 100)

    def test_posted_deposit_journal_items_cannot_change_amount_or_account(self):
        order = self._order()
        order.action_confirm()
        receipt = self._deposit(order)
        receipt.action_post()
        item = receipt.move_id.line_ids.filtered(lambda line: line.account_id == self.deposit_account)
        for values in (
            {'balance': -1}, {'credit': 1}, {'debit': 1},
            {'account_id': self.company_data['default_account_revenue'].id},
        ):
            with self.subTest(values=values), self.assertRaises(UserError), self.env.cr.savepoint():
                item.write(values)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            item.unlink()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.env['account.move.line'].with_context(default_move_id=receipt.move_id.id).create({
                'account_id': self.deposit_account.id, 'name': 'Forged deposit item', 'credit': 1,
            })
        self.assertEqual(item.credit, 100)
        self.assertEqual(item.account_id, self.deposit_account)
        self.assertEqual(order.qing_rental_deposit_balance, 100)

    def test_deposit_requires_rental_and_accounting_roles(self):
        order = self._order()
        order.action_confirm()
        receipt = self._deposit(order)
        manager = new_test_user(
            self.env, login='rental_manager_without_accounting',
            groups='qing_rental.group_rental_manager', company_id=self.env.company.id,
        )
        for user in (self.rental_user, self.sales_user, manager):
            with self.subTest(user=user.login), self.assertRaises(AccessError), self.env.cr.savepoint():
                receipt.with_user(user).action_post()
        self.assertEqual(receipt.state, 'draft')
        self.assertFalse(receipt.move_id)

    def test_cross_company_deposit_post_is_denied(self):
        order = self._order()
        order.action_confirm()
        receipt = self._deposit(order)
        other = self.env['res.company'].create({'name': 'Rental deposit isolated company'})
        manager = new_test_user(
            self.env, login='other_company_deposit_manager',
            groups='qing_rental.group_rental_manager,account.group_account_manager',
            company_id=other.id,
        )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            receipt.with_user(manager).with_context(allowed_company_ids=other.ids).action_post()
        self.assertFalse(receipt.move_id)

    def test_forged_deposit_state_and_accounting_link_are_rejected(self):
        order = self._order()
        for values in ({'state': 'posted'}, {'move_id': 1}):
            with self.subTest(values=values), self.assertRaises(AccessError), self.env.cr.savepoint():
                self._deposit(order, **values)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env['qing.rental.deposit'].with_context(default_state='posted').create({
                'order_id': order.id, 'amount': 100, 'reference': 'FORGED',
                'journal_id': self.deposit_journal.id, 'liability_account_id': self.deposit_account.id,
            })
        receipt = self._deposit(order)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env['account.move'].with_context(_qing_rental_internal=True).create({
                'journal_id': self.deposit_journal.id, 'qing_rental_deposit_id': receipt.id,
            })

    def test_deposit_requires_confirmed_order_and_positive_amount(self):
        order = self._order()
        receipt = self._deposit(order)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            receipt.action_post()
        for amount in (0, -1):
            with self.subTest(amount=amount), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._deposit(order, amount=amount)

    def test_deposit_rejects_foreign_currency_and_non_liability_account(self):
        currency = self.env.ref('base.USD')
        if currency == self.env.company.currency_id:
            currency = self.env.ref('base.EUR')
        currency.active = True
        pricelist = self.env['product.pricelist'].create({
            'name': 'Foreign currency rental price list', 'currency_id': currency.id,
        })
        order = self._order(pricelist_id=pricelist.id)
        order.action_confirm()
        foreign = self._deposit(order)
        self.assertEqual(foreign.currency_id, currency)
        self.assertNotEqual(foreign.currency_id, self.env.company.currency_id)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            foreign.action_post()
        domestic = self._order()
        domestic.action_confirm()
        incorrect = self._deposit(domestic, liability_account_id=self.company_data['default_account_revenue'].id)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            incorrect.action_post()

    def test_outstanding_deposit_must_be_refunded_before_cancellation(self):
        order = self._order()
        order.action_confirm()
        self._deposit(order).action_post()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.action_cancel()
        self._deposit(order, kind='refund').action_post()
        order.action_cancel()
        self.assertEqual(order.state, 'cancel')

    def test_single_line_edit_has_bounded_sql_cost(self):
        counts = []
        for count in (50, 150):
            order = self._order(qing_rental_line_ids=[Command.create({
                'product_id': self.equipment.id, 'quantity': 1, 'rate': 10,
            }) for unused in range(count)])
            order.qing_rental_line_ids.mapped('amount')
            self.env.flush_all()
            target = order.qing_rental_line_ids[-1]
            with self.env.cr.savepoint() as savepoint:
                before = self.env.cr.sql_log_count
                target.write({'rate': 12})
                self.env.flush_all()
                counts.append(self.env.cr.sql_log_count - before)
                self.assertEqual(target.amount, 24)
                self.assertEqual(len(order.qing_rental_line_ids.filtered(lambda line: line.rate == 12)), 1)
                savepoint.rollback()
            self.assertEqual(target.rate, 10)
        _logger.info('Rental single-line write SQL counts: 50 lines=%s; 150 lines=%s', *counts)
        self.assertLessEqual(counts[1], counts[0] + 5, 'One line edit must not rewrite all equipment lines.')
