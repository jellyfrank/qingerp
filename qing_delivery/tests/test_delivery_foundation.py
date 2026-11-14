from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDeliveryFoundation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.shibei = cls.env.ref('qing_address_cn.district_370203')
        cls.subdistrict = cls.env['qing.address.cn.subdistrict'].search(
            [('district_id', '=', cls.shibei.id)], limit=1)
        cls.customer = cls.env['res.partner'].create({
            'name': 'Zhang San',
            'qing_cn_subdistrict_id': cls.subdistrict.id,
            'street': 'Test Road 88',
            'phone': '+86 131-2345-6789',
            'zip': '266000',
        })
        cls.foreign = cls.env['res.partner'].create({
            'name': 'John Doe',
            'country_id': cls.env.ref('base.us').id,
            'city': 'Boston',
            'street': '1 Main St',
            'phone': '+1 617 555 0100',
        })
        cls.fixed_sender = cls.env['res.partner'].create({'name': 'Fixed Sender'})
        product = cls.env['product.product'].create({
            'name': 'Delivery Fee', 'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Test Carrier',
            'delivery_type': 'fixed',
            'product_id': product.id,
        })
        cls.goods = cls.env['product.product'].create({
            'name': 'Tea Cup', 'is_storable': True, 'lst_price': 12.5, 'weight': 0.3,
        })
        warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.warehouse = warehouse
        cls.picking = cls.env['stock.picking'].create({
            'partner_id': cls.customer.id,
            'picking_type_id': warehouse.out_type_id.id,
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': cls.env.ref('stock.stock_location_customers').id,
            'origin': 'S00042',
            'carrier_id': cls.carrier.id,
            'move_ids': [(0, 0, {
                'product_id': cls.goods.id,
                'product_uom_qty': 2,
                'location_id': warehouse.lot_stock_id.id,
                'location_dest_id': cls.env.ref('stock.stock_location_customers').id,
            })],
        })

    def test_china_contact_uses_structured_address(self):
        values = self.carrier._qing_contact_values(self.customer)
        self.assertEqual(values['province'], self.customer.state_id.name)
        self.assertEqual(values['district'], self.shibei.name)
        self.assertEqual(values['subdistrict'], self.subdistrict.name)
        self.assertEqual(values['phone'], '13123456789')
        self.assertEqual(values['country_code'], 'CN')
        self.assertTrue(values['address'].startswith(self.customer.state_id.name))
        self.assertIn(self.shibei.name + self.subdistrict.name, values['address'])
        self.assertTrue(values['address'].endswith('Test Road 88'))

    def test_foreign_contact_keeps_international_phone(self):
        values = self.carrier._qing_contact_values(self.foreign)
        self.assertEqual(values['country_code'], 'US')
        self.assertTrue(values['phone'].startswith('+1'))
        self.assertEqual(values['address'], '1 Main St, Boston')

    def test_check_contact_lists_missing_fields(self):
        with self.assertRaises(UserError) as error:
            self.carrier._qing_check_contact(self.fixed_sender, 'sender')
        self.assertIn('street', str(error.exception))
        self.assertIn('phone', str(error.exception))
        self.carrier._qing_check_contact(self.customer, 'receiver')

    def test_sender_policy(self):
        self.assertEqual(self.carrier._qing_get_sender(picking=self.picking), self.company.partner_id)
        self.carrier.qing_sender_type = 'warehouse'
        self.assertEqual(self.carrier._qing_get_sender(picking=self.picking), self.warehouse.partner_id)
        self.carrier.write({'qing_sender_type': 'fixed', 'qing_fixed_sender_id': self.fixed_sender.id})
        self.assertEqual(self.carrier._qing_get_sender(picking=self.picking), self.fixed_sender)
        self.carrier.qing_fixed_sender_id = False
        with self.assertRaises(UserError):
            self.carrier._qing_get_sender(picking=self.picking)

    def test_reference_policy(self):
        self.assertEqual(self.carrier._qing_get_reference(self.picking), 'S00042')
        self.carrier.qing_reference_type = 'name'
        self.assertEqual(self.carrier._qing_get_reference(self.picking), self.picking.name)
        self.carrier.qing_reference_type = 'both'
        self.assertEqual(self.carrier._qing_get_reference(self.picking), f'S00042-{self.picking.name}')
        self.carrier.qing_reference_type = 'random'
        first = self.carrier._qing_get_reference(self.picking)
        second = self.carrier._qing_get_reference(self.picking)
        self.assertTrue(first.startswith(self.picking.name + '-'))
        self.assertNotEqual(first, second)

    def test_goods_name_and_commodities(self):
        self.assertEqual(self.carrier._qing_get_goods_name(self.picking), 'Goods')
        self.carrier.qing_goods_name = 'Tableware'
        self.assertEqual(self.carrier._qing_get_goods_name(self.picking), 'Tableware')
        self.picking.qing_goods_name = 'Cups'
        self.assertEqual(self.carrier._qing_get_goods_name(self.picking), 'Cups')
        commodities = self.carrier._qing_get_commodities(self.picking)
        self.assertEqual(len(commodities), 1)
        self.assertEqual(commodities[0]['quantity'], 2)
        self.assertEqual(commodities[0]['value'], 25.0)
        self.assertEqual(commodities[0]['currency'], self.company.currency_id.name)

    def test_call_redacts_secrets(self):
        def failing():
            raise ConnectionError('https://api.example.com/token?secret=TopSecret123')

        with patch.object(type(self.carrier), '_qing_secret_values', return_value=['TopSecret123']), \
                self.assertLogs('odoo.addons.qing_delivery.models.delivery_carrier', 'WARNING') as logs, \
                self.assertRaises(UserError) as error:
            self.carrier._qing_call('get token', failing)
        self.assertNotIn('TopSecret123', str(error.exception))
        self.assertIn('***', str(error.exception))
        self.assertNotIn('TopSecret123', '\n'.join(logs.output))

    def test_post_shipment_attaches_labels(self):
        self.carrier._qing_post_shipment(self.picking, 'SF0001', [b'%PDF-1', b'%PDF-2'])
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', 'stock.picking'), ('res_id', '=', self.picking.id)])
        self.assertEqual(
            sorted(attachments.mapped('name')),
            ['LabelShipping-fixed-SF0001-1.pdf', 'LabelShipping-fixed-SF0001-2.pdf'])

    def test_tracking_events_are_stored(self):
        self.picking.carrier_tracking_ref = 'SF0001'
        self.assertFalse(self.picking.qing_tracking_supported)
        with self.assertRaises(UserError):
            self.picking.action_qing_update_tracking()
        events = [
            {'time': '2026-11-14 09:00:00', 'description': 'Picked up', 'location': 'Qingdao'},
            {'time': '2026-11-14 18:00:00', 'description': 'In transit', 'location': ''},
        ]
        with patch.object(type(self.carrier), '_qing_get_tracking_events', return_value=events):
            self.picking.action_qing_update_tracking()
        self.assertEqual(
            self.picking.qing_tracking_detail,
            '2026-11-14 09:00:00 Picked up (Qingdao)\n2026-11-14 18:00:00 In transit')
        self.assertTrue(self.picking.qing_tracking_date)

    def test_more_providers_include_qing_connectors(self):
        action = self.carrier.install_more_provider()
        self.assertEqual(action['domain'][:2], ['|', ['name', '=like', 'qing_delivery_%']])
        modules = self.env['ir.module.module'].search(action['domain'])
        connectors = self.env['ir.module.module'].search([('name', '=like', 'qing_delivery_%')])
        self.assertLessEqual(connectors, modules)
