from lxml import etree

from odoo.fields import Command
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDeliveryStatus(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Delivery Status Partner'})
        cls.product = cls.env['product.product'].create({
            'name': 'Delivery Status Product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.env.company.id),
        ], limit=1)
        cls.env['stock.quant']._update_available_quantity(
            cls.product, cls.warehouse.lot_stock_id, 10
        )

    def _create_order(self, quantity=2):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': quantity,
                'price_unit': 10,
            })],
        })
        order.action_confirm()
        return order

    def _validate(self, picking, quantity):
        picking.action_assign()
        picking.move_ids.write({'quantity': quantity, 'picked': True})
        result = picking.button_validate()
        if isinstance(result, dict) and result.get('res_model') == 'stock.backorder.confirmation':
            Form.from_action(self.env, result).save().process()

    def test_delivery_and_return_states(self):
        order = self._create_order()
        self.assertEqual(order.qing_delivery_status, 'no')

        outgoing = order.picking_ids
        self._validate(outgoing, 1)
        self.assertEqual(order.qing_delivery_status, 'partial')

        backorder = outgoing.backorder_ids
        self._validate(backorder, 1)
        self.assertEqual(order.qing_delivery_status, 'delivered')

        first_return = outgoing._create_return()
        first_return.move_ids.product_uom_qty = 1
        self._validate(first_return, 1)
        self.assertEqual(order.qing_delivery_status, 'partial_return')

        second_return = backorder._create_return()
        second_return.move_ids.product_uom_qty = 1
        self._validate(second_return, 1)
        self.assertEqual(order.qing_delivery_status, 'returned')

    def test_list_view_and_chinese_labels(self):
        for view_name in ('sale.view_order_tree', 'sale.sale_order_list_with_dashboard'):
            view = self.env['sale.order'].with_context(lang='zh_CN').get_view(
                view_id=self.env.ref(view_name).id, view_type='list'
            )
            root = etree.fromstring(view['arch'].encode())
            self.assertEqual(len(root.xpath("//field[@name='qing_delivery_status']")), 1)
            if view_name == 'sale.view_order_tree':
                native_fields = root.xpath("//field[@name='delivery_status']")
                self.assertTrue(all(field.get('column_invisible') == 'True' for field in native_fields))
        labels = dict(self.env['sale.order'].with_context(lang='zh_CN').fields_get(
            ['qing_delivery_status']
        )['qing_delivery_status']['selection'])
        self.assertEqual(labels, {
            'no': '未发货',
            'partial': '部分发货',
            'delivered': '已发货',
            'partial_return': '部分退货',
            'returned': '已退货',
        })
