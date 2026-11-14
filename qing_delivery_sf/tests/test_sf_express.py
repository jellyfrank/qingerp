from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

CLIENT_PATH = 'odoo.addons.qing_delivery_sf.models.delivery_carrier.SF'


def _fake_client():
    client = MagicMock()
    client.order.create_order.return_value = {
        'success': True,
        'msgData': {'waybillNoInfoList': [{'waybillNo': 'SF1234567890'}]},
    }
    client.order.confirm_order.return_value = {'success': True}
    client.order.get_route_info.return_value = {
        'success': True,
        'msgData': {'routeResps': [{'routes': [
            {'acceptTime': '2026-11-14 09:00:00', 'remark': 'Picked up', 'acceptAddress': 'Qingdao'},
            {'acceptTime': '2026-11-14 20:00:00', 'remark': 'Departed', 'acceptAddress': 'Jinan'},
        ]}]},
    }
    client.order.query_delivery.return_value = {
        'success': True, 'msgData': {'deliverTmDto': [{'fee': 23.0}]},
    }
    client.sheet.sync_print.return_value = [b'%PDF-label']
    client.sheet.get_custom_templates.return_value = {
        'success': True,
        'obj': [{'customTemplateCode': 'fm_150_custom_1', 'customTemplateName': 'Custom 150'}],
    }
    return client


@tagged('post_install', '-at_install')
class TestSfExpress(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.partner_id.write({
            'qing_cn_district_id': cls.env.ref('qing_address_cn.district_370203').id,
            'street': 'Sender Road 1',
            'phone': '0532 8888 6666',
        })
        cls.receiver = cls.env['res.partner'].create({
            'name': 'Li Si',
            'qing_cn_district_id': cls.env.ref('qing_address_cn.district_370102').id,
            'street': 'Receiver Street 9',
            'phone': '13900001111',
        })
        cls.carrier = cls.env.ref('qing_delivery_sf.delivery_carrier_sf')
        cls.carrier.write({
            'sf_client_code': 'QINGTEST',
            'sf_sandbox_checkword': 'SandboxSecret42',
            'sf_monthly_card': '7551234567',
            'qing_goods_name': 'Tea',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Tea Pot', 'is_storable': True, 'lst_price': 99.0, 'weight': 1.2,
        })
        warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.env['stock.quant']._update_available_quantity(cls.product, warehouse.lot_stock_id, 10)
        cls.warehouse = warehouse

    def _picking(self, **values):
        customers = self.env.ref('stock.stock_location_customers')
        return self.env['stock.picking'].create({
            'partner_id': self.receiver.id,
            'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': customers.id,
            'origin': 'S00077',
            'carrier_id': self.carrier.id,
            'move_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 2,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': customers.id,
            })],
            **values,
        })

    def test_carrier_defaults(self):
        self.assertEqual(self.carrier.delivery_type, 'sf')
        self.assertEqual(self.carrier.sf_template_id, self.env.ref('qing_delivery_sf.sf_template_fm_150_standard'))
        self.assertFalse(self.carrier.prod_environment)
        self.assertTrue(self.carrier._qing_tracking_supported())

    def test_validate_creates_waybill_and_label(self):
        picking = self._picking()
        picking.action_confirm()
        picking.action_assign()
        client = _fake_client()
        with patch(CLIENT_PATH, return_value=client) as factory:
            picking.button_validate()
        factory.assert_called_once_with('QINGTEST', 'SandboxSecret42', sandbox=True, language=self.carrier._sf_language())
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.carrier_tracking_ref, 'SF1234567890')
        self.assertEqual(picking.qing_carrier_order_ref, 'S00077')
        args, kwargs = client.order.create_order.call_args
        self.assertEqual(args[0], 'S00077')
        receiver, sender = (contact.to_dict() for contact in args[1])
        self.assertEqual(receiver['contactType'], 2)
        self.assertEqual(receiver['mobile'], '13900001111')
        self.assertEqual(receiver['province'], self.receiver.state_id.name)
        self.assertIn('Receiver Street 9', receiver['address'])
        self.assertEqual(sender['contactType'], 1)
        self.assertEqual(sender['mobile'], '053288886666')
        self.assertEqual(kwargs['cargoDesc'], 'Tea')
        self.assertEqual(kwargs['monthlyCard'], '7551234567')
        self.assertEqual(kwargs['expressTypeId'], 1)
        self.assertNotIn('customsInfo', kwargs)
        self.assertEqual(args[2][0].to_dict()['cargoDeclaredValue'], 198.0)
        client.sheet.sync_print.assert_called_once_with(
            'fm_150_standard_QINGTEST', [{'masterWaybillNo': 'SF1234567890'}])
        label = self.env['ir.attachment'].search([
            ('res_model', '=', 'stock.picking'), ('res_id', '=', picking.id),
            ('name', '=', 'LabelShipping-sf-SF1234567890.pdf')])
        self.assertTrue(label)
        self.assertIn('SF1234567890', picking.carrier_tracking_url)

    def test_manual_order_and_cancel_reuse_reference(self):
        self.carrier.write({'sf_manual_order': True, 'qing_reference_type': 'random'})
        picking = self._picking()
        picking.action_confirm()
        client = _fake_client()
        with patch(CLIENT_PATH, return_value=client):
            picking.action_sf_order()
            reference = picking.qing_carrier_order_ref
            self.assertTrue(reference.startswith(picking.name + '-'))
            picking.cancel_shipment()
        client.order.confirm_order.assert_called_once_with(reference, dealType=2)
        self.assertFalse(picking.carrier_tracking_ref)
        self.assertFalse(picking.qing_carrier_order_ref)
        self.assertTrue(any('SF1234567890' in (m.body or '') and 'cancelled' in m.body for m in picking.message_ids))

    def test_label_failure_keeps_waybill(self):
        picking = self._picking()
        picking.action_confirm()
        client = _fake_client()
        client.sheet.sync_print.side_effect = Exception('template not authorized')
        with patch(CLIENT_PATH, return_value=client):
            picking.send_to_shipper()
        self.assertEqual(picking.carrier_tracking_ref, 'SF1234567890')
        self.assertTrue(any('template not authorized' in message.body for message in picking.message_ids))
        client.sheet.sync_print.side_effect = None
        with patch(CLIENT_PATH, return_value=client):
            picking.action_sf_print_label()
        self.assertTrue(self.env['ir.attachment'].search_count([
            ('res_model', '=', 'stock.picking'), ('res_id', '=', picking.id),
            ('name', '=like', 'LabelShipping-sf-%')]))

    def test_api_error_is_readable_and_redacted(self):
        picking = self._picking()
        picking.action_confirm()
        client = _fake_client()
        client.order.create_order.side_effect = Exception('token error for secret=SandboxSecret42')
        with patch(CLIENT_PATH, return_value=client), self.assertRaises(UserError) as error:
            picking.send_to_shipper()
        self.assertNotIn('SandboxSecret42', str(error.exception))
        client.order.create_order.side_effect = None
        client.order.create_order.return_value = {'success': False, 'errorMsg': 'address out of range'}
        with patch(CLIENT_PATH, return_value=client), self.assertRaises(UserError) as error:
            picking.send_to_shipper()
        self.assertIn('address out of range', str(error.exception))

    def test_missing_receiver_phone(self):
        self.receiver.phone = False
        picking = self._picking()
        with patch(CLIENT_PATH, return_value=_fake_client()), self.assertRaises(UserError) as error:
            picking.send_to_shipper()
        self.assertIn('phone', str(error.exception))

    def test_cross_border_declares_value(self):
        self.receiver.write({
            'country_id': self.env.ref('base.us').id, 'city': 'Boston', 'state_id': False,
            'phone': '+1 617 555 0100',
        })
        self.carrier.write({'sf_declared_value': 50.0, 'sf_declared_currency_id': self.env.ref('base.USD').id})
        picking = self._picking()
        client = _fake_client()
        with patch(CLIENT_PATH, return_value=client):
            picking.send_to_shipper()
        args, kwargs = client.order.create_order.call_args
        self.assertEqual(kwargs['customsInfo'], {'declaredValue': 50.0, 'declaredValueCurrency': 'USD'})
        cargo = args[2][0].to_dict()
        self.assertEqual(cargo['count'], 2)
        self.assertEqual(args[1][0].to_dict()['country'], 'US')

    def test_transfer_overrides(self):
        self.carrier.write({'sf_select_express_type': True, 'sf_payment_select': True})
        picking = self._picking(sf_express_type='2', sf_payment_method='2')
        client = _fake_client()
        with patch(CLIENT_PATH, return_value=client):
            picking.send_to_shipper()
        kwargs = client.order.create_order.call_args.kwargs
        self.assertEqual(kwargs['expressTypeId'], 2)
        self.assertEqual(kwargs['payMethod'], 2)

    def test_tracking_refresh(self):
        picking = self._picking()
        picking.carrier_tracking_ref = 'SF1234567890'
        with patch(CLIENT_PATH, return_value=_fake_client()):
            picking.action_qing_update_tracking()
        self.assertEqual(
            picking.qing_tracking_detail,
            '2026-11-14 09:00:00 Picked up (Qingdao)\n2026-11-14 20:00:00 Departed (Jinan)')

    def test_rate_shipment(self):
        order = self.env['sale.order'].create({
            'partner_id': self.receiver.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })
        client = _fake_client()
        with patch(CLIENT_PATH, return_value=client):
            result = self.carrier.sf_rate_shipment(order)
        self.assertTrue(result['success'])
        expected = self.env.ref('base.CNY')._convert(23.0, self.company.currency_id, self.company, order.date_order)
        self.assertAlmostEqual(result['price'], expected, places=2)
        dest = client.order.query_delivery.call_args.args[1].to_dict()
        self.assertEqual(dest['district'], self.receiver.qing_cn_district_id.name)

    def test_template_sync(self):
        client = _fake_client()
        with patch(CLIENT_PATH, return_value=client):
            self.carrier.action_sf_sync_templates()
            self.carrier.action_sf_sync_templates()
        templates = self.env['qing.delivery.sf.template'].search([('code', '=', 'fm_150_custom_1')])
        self.assertEqual(len(templates), 1)
        self.assertTrue(templates.custom)

    def test_language_mapping(self):
        self.assertEqual(self.carrier.with_context(lang='en_US')._sf_language(), 'en')
        self.assertEqual(self.carrier.with_context(lang='zh_CN')._sf_language(), 'zh-CN')
        self.assertEqual(self.carrier._sf_language_code('zh_TW'), 'zh-TW')
        self.assertEqual(self.carrier._sf_language_code('zh_HK'), 'zh-HK')
        self.assertEqual(self.carrier._sf_language_code('fr_FR'), 'zh-CN')
        self.carrier.write({'sf_language_type': 'fixed', 'sf_language': 'zh-HK'})
        self.assertEqual(self.carrier.with_context(lang='en_US')._sf_language(), 'zh-HK')

    def test_missing_credentials(self):
        self.carrier.sf_sandbox_checkword = False
        with self.assertRaises(UserError):
            self.carrier._sf_client()

    def test_stock_user_can_ship(self):
        user = self.env['res.users'].create({
            'name': 'Stock Clerk', 'login': 'sf_stock_clerk',
            'group_ids': [(6, 0, [self.env.ref('stock.group_stock_user').id])],
        })
        picking = self._picking().with_user(user)
        client = _fake_client()
        with patch(CLIENT_PATH, return_value=client):
            picking.send_to_shipper()
        self.assertEqual(picking.carrier_tracking_ref, 'SF1234567890')

    def test_carrier_form_hides_credentials_for_stock_manager(self):
        manager = self.env['res.users'].create({
            'name': 'Stock Manager', 'login': 'sf_stock_manager',
            'group_ids': [(6, 0, [self.env.ref('stock.group_stock_manager').id])],
        })
        views = self.env['delivery.carrier'].with_user(manager).get_views([(False, 'form')])
        fields = views['models']['delivery.carrier']['fields']
        self.assertNotIn('sf_checkword', fields)
        self.assertIn('sf_template_id', fields)
