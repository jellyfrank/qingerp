from datetime import timedelta

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

from ..models.common import internal


@tagged('post_install', '-at_install')
class TestRentalAmount(AccountTestInvoicingCommon):

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
                'name': 'Rental amount tests', 'code': 'RAT', 'company_id': cls.env.company.id,
            })
        cls.warehouse.action_qing_setup_rental()
        cls.service = cls._create_product(
            name='Rental amount fee', type='service', invoice_policy='order',
            uom_id=cls.uom_unit.id, taxes_id=[Command.clear()],
        )
        cls.equipment = cls._create_product(
            name='Rental amount equipment', type='consu', is_storable=True,
            uom_id=cls.uom_unit.id, qing_rental_ok=True,
            qing_rental_service_id=cls.service.id, qing_rental_rate=200,
            taxes_id=[Command.clear()],
        )
        cls.env['stock.quant']._update_available_quantity(cls.equipment, cls.warehouse.qing_rental_stock_location_id, 10)

    def _quotation(self, quantity=2):
        start = fields.Datetime.now() - timedelta(hours=1)
        return self.env['sale.order'].create({
            'partner_id': self.partner_a.id, 'warehouse_id': self.warehouse.id,
            'company_id': self.env.company.id, 'is_qing_rental': True,
            'qing_rental_start': start, 'qing_rental_end': start + timedelta(days=1),
            'qing_rental_line_ids': [Command.create({'product_id': self.equipment.id, 'quantity': quantity})],
        })

    def _validate_transfer(self, action, quantity):
        picking = self.env['stock.picking'].browse(action['res_id'])
        picking.move_ids.quantity = quantity
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')

    def test_partial_return_renewal_amount_matches_actual_discounted_charges(self):
        order = self._quotation()
        line = order.qing_rental_line_ids
        order.action_qing_rental_reprice()
        rent = line.sales_line_ids
        rent.discount = 10
        self.assertEqual(line.amount, 400, 'A draft still shows its equipment estimate.')
        order.action_confirm()
        self.assertEqual(rent.discount, 10)
        self.assertEqual(line.amount, 360)
        self._validate_transfer(order.action_qing_rental_pickup(), 2)
        self._validate_transfer(order.action_qing_rental_return(), 1)
        self.env['qing.rental.renew'].create({
            'order_id': order.id, 'new_end': order.qing_rental_end + timedelta(days=1),
        }).action_apply()
        renewal = line.sales_line_ids.filtered(lambda charge: charge.qing_rental_charge == 'renewal')
        self.assertEqual(renewal.product_uom_qty, 1)
        self.assertEqual(renewal.discount, 10)
        self.assertEqual(renewal.price_subtotal, 180)
        self.assertEqual(line.amount, 540)
        self.env['sale.order.line'].create({
            'order_id': order.id, 'product_id': self.service.id, 'product_uom_qty': 1,
            'price_unit': 40, 'name': 'Extra service unrelated to rental charges',
        })
        self.assertEqual(order.amount_untaxed, 580)
        self.assertEqual(line.amount, 540, 'Unlinked services must not enter the equipment amount.')
        # Trusted workflow corrections must also invalidate the displayed amount.
        internal(rent).write({'discount': 20})
        self.assertEqual(line.amount, 500)
        included_tax = self.env['account.tax'].create({
            'name': 'Included rental tax 25%', 'amount': 25, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'price_include_override': 'tax_included', 'company_id': self.env.company.id,
        })
        renewal.tax_ids = [Command.set(included_tax.ids)]
        self.assertEqual(renewal.price_subtotal, 144)
        self.assertEqual(line.amount, 464, 'Native untaxed subtotals must drive confirmed amounts.')

    def test_draft_and_sent_amount_remain_live_estimates(self):
        order = self._quotation()
        line = order.qing_rental_line_ids
        self.assertEqual(line.amount, 400)
        order.action_qing_rental_reprice()
        old_charge_amount = line.sales_line_ids.price_subtotal
        line.write({'rate': 250, 'quantity': 3})
        self.assertEqual(line.amount, 750)
        self.assertEqual(line.sales_line_ids.price_subtotal, old_charge_amount)
        order.qing_rental_end += timedelta(days=1)
        self.assertEqual(line.amount, 1500)
        order.write({'state': 'sent'})
        line.rate = 300
        self.assertEqual(line.amount, 1800)
        order.action_confirm()
        self.assertEqual(line.amount, 1800)
        self.assertEqual(line.amount, line.sales_line_ids.price_subtotal)

    def test_cancelled_amount_retains_actual_charges_and_reset_restores_estimate(self):
        order = self._quotation()
        line = order.qing_rental_line_ids
        order.action_qing_rental_reprice()
        line.sales_line_ids.discount = 25
        order.action_confirm()
        self.assertEqual(line.amount, 300)
        order.action_cancel()
        self.assertEqual(line.amount, 300)
        order.action_draft()
        self.assertEqual(line.amount, 400)
