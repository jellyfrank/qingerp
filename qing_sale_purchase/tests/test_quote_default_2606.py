from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQuoteDefault2606(TransactionCase):
    def test_product_default_requires_confirmed_order(self):
        if self.env['ir.module.module'].search_count([('name', '=', 'bgwy_purchase'), ('state', '=', 'installed')]):
            self.skipTest('Product default is verified without the customer policy module.')
        partner = self.env['res.partner'].create({'name': '2606 Default Customer'})
        product = self.env['product.product'].create({'name': '2606 Default Product', 'type': 'consu', 'purchase_ok': True})
        order = self.env['sale.order'].create({'partner_id': partner.id, 'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 5})]})
        self.assertEqual(order._qing_purchase_allowed_states(), ('sale',))
        self.assertFalse(order.qing_can_create_purchase)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.action_qing_create_purchase()
