from unittest.mock import Mock, patch

from Crypto.PublicKey import RSA
from lxml import html

from odoo.addons.payment.tests.common import PaymentCommon
from odoo.addons.qing_payment_alipay.controllers.controllers import Alipay
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import BinaryBytes


@tagged('post_install', '-at_install')
class TestAlipay(PaymentCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.enable_process_patcher = False
        cls.provider = cls._prepare_provider('alipay')
        cls.provider.is_global = False
        cls.payment_method = cls.env.ref('qing_payment_alipay.payment_method_alipay')
        cls.payment_method_id = cls.payment_method.id
        cls.currency = cls._enable_currency('CNY')
        cls.key = RSA.generate(2048)

    def _payload(self, tx, **values):
        return dict({
            'out_trade_no': tx.reference, 'trade_no': 'verified-trade',
            'trade_status': 'TRADE_SUCCESS', 'total_amount': str(tx.amount),
            'code': '10000',
        }, **values)

    def _process(self, tx, data):
        tx._record(data)
        queued = tx.payment_data_ids
        self.assertEqual(len(queued), 1)
        tx.with_context(payment_safe_write=True)._process(queued.payload)
        queued.unlink()

    def test_domestic_queue_and_duplicate(self):
        tx = self._create_transaction('redirect')
        self._process(tx, self._payload(tx))
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, 'verified-trade')
        self._process(tx, self._payload(tx))
        self.assertEqual(tx.state, 'done')

    def test_domestic_states(self):
        for index, (status, expected) in enumerate([
            ('WAIT_BUYER_PAY', 'pending'), ('TRADE_CLOSED', 'cancel'),
            ('TRADE_FINISHED', 'done'), ('UNKNOWN', 'error'),
        ]):
            tx = self._create_transaction('redirect', reference=f'STATE-{index}')
            self._process(tx, self._payload(tx, trade_status=status))
            self.assertEqual(tx.state, expected)

    def test_amount_mismatch_and_missing(self):
        for index, amount in enumerate(['0.01', None]):
            tx = self._create_transaction('redirect', reference=f'AMOUNT-{index}')
            self._process(tx, self._payload(tx, total_amount=amount))
            self.assertEqual(tx.state, 'error')

    def test_reference_cannot_be_rewritten(self):
        tx = self._create_transaction('redirect')
        reference = tx.reference
        with self.assertRaises(ValidationError):
            tx.with_context(payment_safe_write=True)._process(self._payload(tx, out_trade_no='other'))
        self.assertEqual(tx.reference, reference)

    def test_global_query_records_server_amount(self):
        self.provider.is_global = True
        tx = self._create_transaction('redirect', amount=12.34, currency_id=self.currency_usd.id)
        client = Mock()
        client.payment.inquiry.return_value = {
            'result': {'resultStatus': 'S'}, 'paymentStatus': 'SUCCESS',
            'paymentId': 'antom-id', 'paymentAmount': {'value': '1234', 'currency': 'USD'},
        }
        with patch.object(type(self.provider), '_get_alipay', return_value=client):
            self.assertTrue(tx._alipay_query_and_record())
        self.assertEqual(tx.state, 'draft')
        tx.with_context(payment_safe_write=True)._process(tx.payment_data_ids.payload)
        self.assertEqual(tx.state, 'done')
        client.payment.inquiry.assert_called_once_with(paymentRequestId=tx.reference)

    def test_global_states_and_currency(self):
        self.provider.is_global = True
        for index, (status, currency, expected) in enumerate([
            ('PAYMENT_IN_PROCESS', 'USD', 'pending'), ('CANCELLED', 'USD', 'cancel'),
            ('FAIL', 'USD', 'error'), ('SUCCESS', 'EUR', 'error'),
        ]):
            tx = self._create_transaction('redirect', reference=f'GLOBAL-{index}', amount=12.34,
                                          currency_id=self.currency_usd.id)
            self._process(tx, {
                'paymentRequestId': tx.reference, 'paymentId': 'antom-id',
                'paymentStatus': status, 'paymentAmount': {'value': '1234', 'currency': currency},
            })
            self.assertEqual(tx.state, expected)

    def test_global_zero_decimal_currency(self):
        self.provider.is_global = True
        tx = self._create_transaction('redirect', amount=1234, currency_id=self._enable_currency('JPY').id)
        self._process(tx, {
            'paymentRequestId': tx.reference, 'paymentId': 'antom-id', 'paymentStatus': 'SUCCESS',
            'paymentAmount': {'value': '1234', 'currency': 'JPY'},
        })
        self.assertEqual(tx.state, 'done')
        client = Mock()
        client.payment.pay.return_value = {'result': {'resultStatus': 'U'},
                                         'redirectActionForm': {'redirectUrl': 'https://example.com/pay'}}
        self.provider._get_global_api_url(client, 1234, tx.reference, 'JPY')
        self.assertEqual(client.payment.pay.call_args.kwargs['paymentAmount']['value'], 1234)

    def test_url_query_round_trip_and_queryless(self):
        tx = self._create_transaction('redirect')
        for url, params in [
            ('https://example.com/pay?sign=a%2Bb%3D%3D&empty=&x=1&x=2',
             [('sign', 'a+b=='), ('empty', ''), ('x', '1'), ('x', '2')]),
            ('https://example.com/pay', []),
        ]:
            with patch.object(type(self.provider), '_alipay_get_api_url', return_value=url):
                values = tx._get_specific_rendering_values({})
            self.assertEqual(values['params'], params)
            rendered = self.env['ir.qweb']._render('qing_payment_alipay.redirect_form', values)
            doc = html.fromstring(str(rendered))
            self.assertEqual(doc.get('action'), 'https://example.com/pay')
            self.assertEqual([(n.get('name'), n.get('value')) for n in doc.xpath('.//input')], params)

    def test_uploaded_binary_keys_domestic_and_global(self):
        private, public = BinaryBytes(self.key.export_key()), BinaryBytes(self.key.public_key().export_key())
        self.provider.write({
            'alipay_secret': private, 'alipay_public_key': public, 'alipay_appid': 'prod',
            'alipay_sandbox_secret': private, 'alipay_sandbox_public_key': public,
            'alipay_sandbox_appid': 'sandbox', 'alipay_sandbox_sign_type': 'rsa2',
            'antom_private_key': private, 'antom_public_key': public,
            'antom_sandbox_private_key': private, 'antom_sandbox_public_key': public,
        })
        for sandbox in [False, True]:
            local = self.provider._get_local_instance(sandbox)
            self.assertEqual(local.appid, 'sandbox' if sandbox else 'prod')
            self.assertEqual(local.app_private_key.n, self.key.n)
            self.assertEqual(self.provider._get_global_instance(sandbox).private_key.n, self.key.n)

    def test_invalid_key_has_configuration_error(self):
        self.provider.alipay_secret = BinaryBytes(b'not a key')
        with self.assertRaises(UserError):
            self.provider._get_local_instance()

    def test_signature_sandbox_identity_and_payload_preserved(self):
        self.provider.write({'is_live': False, 'alipay_sandbox_appid': 'app', 'sandbox_seller_id': 'seller'})
        client = Mock()
        data = {'app_id': 'app', 'seller_id': 'seller', 'sign': 'test'}
        def verify(values):
            values.pop('sign')
            return True
        client.comm.validate_sign.side_effect = verify
        with patch.object(type(self.provider), '_get_alipay', return_value=client):
            self.assertTrue(self.provider._validate_payment_data(data))
            self.assertFalse(self.provider._validate_payment_data(dict(data, seller_id='other')))
            client.comm.validate_sign.side_effect = ValueError('malformed')
            self.assertFalse(self.provider._validate_payment_data(data))
        self.assertIn('sign', data)

    def test_notify_selects_transaction_provider_and_queues(self):
        tx = self._create_transaction('redirect')
        req = Mock(env=self.env)
        req.httprequest.mimetype = 'application/x-www-form-urlencoded'
        with patch('odoo.addons.qing_payment_alipay.controllers.controllers.request', req), \
             patch.object(type(self.provider), '_validate_payment_data', return_value=True) as validate:
            result = Alipay.alipay_notify.__wrapped__(Alipay(), **self._payload(tx))
        self.assertEqual(result, 'success')
        validate.assert_called_once()
        self.assertEqual(len(tx.payment_data_ids), 1)
        self.assertEqual(tx.state, 'draft')

    def test_global_notify_ignores_untrusted_success(self):
        self.provider.is_global = True
        tx = self._create_transaction('redirect')
        req = Mock(env=self.env)
        req.httprequest.mimetype = 'application/json'
        req.httprequest.get_json.return_value = {'paymentRequestId': tx.reference, 'paymentStatus': 'SUCCESS'}
        client = Mock()
        client.payment.inquiry.return_value = {'result': {'resultStatus': 'U'}}
        with patch('odoo.addons.qing_payment_alipay.controllers.controllers.request', req), \
             patch.object(type(self.provider), '_get_alipay', return_value=client):
            Alipay.alipay_notify.__wrapped__(Alipay())
        self.assertFalse(tx.payment_data_ids)
        self.assertEqual(tx.state, 'draft')
        req.make_response.assert_called_once_with('failed', status=503)

    def test_refund_records_positive_amount_on_negative_child(self):
        source = self._create_transaction('redirect', amount=12.34)
        self._process(source, self._payload(source))
        client = Mock()
        client.pay.trade_refund.return_value = {'code': '10000', 'trade_no': 'verified-trade', 'refund_fee': '2.00'}
        with patch.object(type(self.provider), '_get_alipay', return_value=client):
            tx = source._refund(amount_to_refund=2)
        self.assertEqual(tx.state, 'draft')
        tx.with_context(payment_safe_write=True)._process(tx.payment_data_ids.payload)
        self.assertEqual(tx.state, 'done')
        client.pay.trade_refund.assert_called_once_with(2, trade_no='verified-trade', out_request_no=tx.reference)

    def test_mode_switch_refreshes_available_currencies(self):
        self.provider.is_global = False
        self.assertEqual(self.provider.available_currency_ids.mapped('name'), ['CNY'])
        self.provider.is_global = True
        self.assertIn(self.currency_usd, self.provider.available_currency_ids)

    def test_return_uses_monitored_transaction_not_url_reference(self):
        tx = self._create_transaction('redirect')
        controller = Alipay()
        req = Mock()
        with patch('odoo.addons.qing_payment_alipay.controllers.controllers.request', req), \
             patch.object(controller, '_get_monitored_transaction', return_value=tx), \
             patch.object(type(tx), '_alipay_query_and_record', return_value=True) as query:
            controller.alipay_validate.__wrapped__(controller, out_trade_no='untrusted-other-order')
        query.assert_called_once()
        req.redirect.assert_called_once_with('/payment/status')

    def test_return_inquiry_sdk_failure_does_not_raise_500(self):
        tx = self._create_transaction('redirect')
        client = Mock()
        client.pay.trade_query.side_effect = Exception('SDK wraps non-JSON gateway response')
        controller = Alipay()
        req = Mock()
        with patch('odoo.addons.qing_payment_alipay.controllers.controllers.request', req), \
             patch.object(controller, '_get_monitored_transaction', return_value=tx), \
             patch.object(type(self.provider), '_get_alipay', return_value=client):
            controller.alipay_validate.__wrapped__(controller)
        req.redirect.assert_called_once_with('/payment/status')
        self.assertEqual(tx.state, 'draft')
        self.assertFalse(tx.payment_data_ids)

    def test_malformed_inquiry_responses_are_configuration_errors(self):
        tx = self._create_transaction('redirect')
        client = Mock()
        with patch.object(type(self.provider), '_get_alipay', return_value=client):
            for response in (None, [], '<html>error</html>'):
                client.pay.trade_query.return_value = response
                with self.assertRaises(UserError):
                    tx._alipay_query_and_record()
            self.provider.is_global = True
            client.payment.inquiry.return_value = {'result': None}
            with self.assertRaises(UserError):
                tx._alipay_query_and_record()
        self.assertFalse(tx.payment_data_ids)

    def test_notification_invalid_signature_does_not_queue(self):
        tx = self._create_transaction('redirect')
        req = Mock(env=self.env)
        req.httprequest.mimetype = 'application/x-www-form-urlencoded'
        with patch('odoo.addons.qing_payment_alipay.controllers.controllers.request', req), \
             patch.object(type(self.provider), '_validate_payment_data', return_value=False):
            Alipay.alipay_notify.__wrapped__(Alipay(), **self._payload(tx))
        self.assertFalse(tx.payment_data_ids)
        req.make_response.assert_called_once_with('failed', status=400)

    def test_global_refund_is_advertised(self):
        self.assertEqual(self.provider.support_refund, 'partial')
        self.provider.is_global = True
        self.assertEqual(self.provider.support_refund, 'partial')

    def test_provider_view(self):
        self.assertTrue(self.provider.get_view(view_type='form')['arch'])

    def test_refund_button_visibility_and_confirmation(self):
        from odoo.tools.safe_eval import safe_eval

        tx = self._create_transaction('redirect')
        arch = html.fromstring(tx.get_view(view_type='form')['arch'])
        buttons = arch.xpath(
            "//header/button[@name='action_refund' and "
            "contains(@invisible, \"provider_code != 'alipay'\")]"
        )
        self.assertEqual(len(buttons), 1)
        button = buttons[0]
        self.assertEqual(button.get('type'), 'object')
        self.assertIn("full amount", button.get("confirm"))
        self.assertTrue(arch.xpath("//field[@name='operation']"))
        values = dict(provider_code='alipay', state='done', operation='online_redirect',
                      amount=12, provider_reference='verified-payment')
        self.assertFalse(safe_eval(button.get('invisible'), values))
        for changes in ({'state': 'pending'}, {'state': 'draft'}, {'state': 'error'},
                        {'state': 'cancel'}, {'operation': 'refund'}, {'amount': 0},
                        {'provider_reference': False}, {'provider_code': 'wechatpay'}):
            self.assertTrue(safe_eval(button.get('invisible'), dict(values, **changes)))

    def test_refund_button_calls_native_domestic_refund(self):
        source = self._create_transaction('redirect', amount=12)
        self._process(source, self._payload(source))
        client = Mock()
        client.pay.trade_refund.return_value = {
            'code': '10000', 'trade_no': 'verified-trade', 'refund_fee': '12.00'}
        with patch.object(type(self.provider), '_get_alipay', return_value=client):
            source.action_refund()
        refund = source.child_transaction_ids.filtered(lambda tx: tx.operation == 'refund')
        self.assertEqual(refund.amount, -12)
        client.pay.trade_refund.assert_called_once_with(
            12, trade_no='verified-trade', out_request_no=refund.reference)
