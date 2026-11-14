import base64
import json
from unittest.mock import Mock, patch
from urllib.parse import quote

from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from requests.exceptions import Timeout

from odoo.addons.payment.tests.common import PaymentCommon
from odoo.addons.qing_payment_alipay.controllers.controllers import Alipay
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tools import BinaryBytes


@tagged('post_install', '-at_install')
class TestAntomRefund(PaymentCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.enable_process_patcher = False
        cls.provider = cls._prepare_provider('alipay')
        cls.payment_method = cls.env.ref('qing_payment_alipay.payment_method_alipay')
        cls.payment_method_id = cls.payment_method.id
        cls.key = RSA.generate(2048)
        cls.provider.write({
            'is_global': True, 'is_live': False, 'antom_sandbox_client_id': 'refund-test',
            'antom_sandbox_private_key': BinaryBytes(cls.key.export_key()),
            'antom_sandbox_public_key': BinaryBytes(cls.key.public_key().export_key()),
        })

    def _source(self, **values):
        return self._create_transaction('redirect', **dict({
            'amount': 12.34, 'currency_id': self.currency_usd.id,
            'state': 'done', 'provider_reference': 'original-payment',
        }, **values))

    def _data(self, tx, **values):
        return dict({
            'result': {'resultStatus': 'S', 'resultCode': 'SUCCESS'},
            'refundRequestId': tx.antom_refund_request_id, 'refundId': 'refund-' + str(tx.id),
            'paymentId': tx.source_transaction_id.provider_reference,
            'refundAmount': tx.antom_refund_payload['refundAmount'], 'refundStatus': 'SUCCESS',
        }, **values)

    def _drain(self, tx):
        for item in tx.payment_data_ids:
            tx.with_context(payment_safe_write=True)._process(item.payload)
            item.unlink()

    def _poll(self, tx, response=None, error=None):
        client = tx._antom_refund_client()
        with patch.object(type(client.payment), 'refund', return_value=response, side_effect=error) as send:
            tx.with_context(payment_safe_write=True)._antom_poll_refund()
        return send

    def test_partial_refund_is_queued_before_network(self):
        source = self._source()
        with patch('requests.post', side_effect=AssertionError('No HTTP before commit')):
            tx = source._refund(2)
        self.assertEqual(tx.amount, -2)
        self.assertEqual(tx.state, 'pending')
        self.assertEqual(tx.antom_refund_phase, 'queued')
        self.assertEqual(len(tx.antom_refund_request_id), 32)
        send = self._poll(tx, self._data(tx))
        send.assert_called_once_with(**tx.antom_refund_payload)
        self.assertEqual(tx.state, 'pending')
        self._drain(tx)
        self.assertEqual((tx.state, tx.antom_refund_phase), ('done', 'done'))
        self.assertEqual(source.state, 'done')
        self.assertEqual(tx.provider_reference, 'refund-' + str(tx.id))

    def test_full_refund_and_no_over_refund(self):
        source = self._source()
        tx = source._refund()
        self.assertEqual(tx.amount, -source.amount)
        with self.assertRaises(ValidationError):
            source._refund(0.01)
        self._poll(tx, self._data(tx))
        self._drain(tx)
        self.assertEqual(tx.state, 'done')

    def test_pending_refunds_reserve_amount_and_multiple_partials(self):
        source = self._source()
        first = source._refund(10)
        with self.assertRaises(ValidationError):
            source._refund(3)
        second = source._refund(2.34)
        self.assertNotEqual(first.antom_refund_request_id, second.antom_refund_request_id)
        with self.assertRaises(ValidationError):
            source._refund(0.01)

    def test_invalid_amount_or_source_rejected(self):
        source = self._source()
        for value in (0, -1, 0.001, float('nan'), float('inf')):
            with self.assertRaises(ValidationError):
                source._refund(value)
        source.with_context(payment_safe_write=True).write({'state': 'pending'})
        with self.assertRaises(ValidationError):
            source._refund(1)

    def test_timeout_queries_same_id_never_creates_new_refund(self):
        tx = self._source()._refund(2)
        request_id = tx.antom_refund_request_id
        self._poll(tx, error=Timeout())
        self.assertEqual(tx.antom_refund_phase, 'query')
        self.assertEqual(tx.state, 'pending')
        client = tx._antom_refund_client()
        with patch.object(type(client.payment), 'refund') as send, \
             patch.object(type(client.payment), 'inquiry_refund', return_value=self._data(tx)) as query:
            tx.with_context(payment_safe_write=True)._antom_poll_refund()
        send.assert_not_called()
        query.assert_called_once_with(refundRequestId=request_id)
        self._drain(tx)
        self.assertEqual(tx.state, 'done')

    def test_query_success_is_not_refund_success(self):
        tx = self._source()._refund(2)
        self._poll(tx, {'result': {'resultStatus': 'U', 'resultCode': 'REFUND_IN_PROCESS'}})
        self._drain(tx)
        for status in ('PROCESSING', 'FAIL'):
            tx._antom_record_refund(self._data(tx, refundStatus=status), 'query')
            self._drain(tx)
            self.assertEqual(tx.state, 'pending' if status == 'PROCESSING' else 'error')

    def test_query_failure_does_not_release_reserved_funds(self):
        source = self._source()
        tx = source._refund()
        self._poll(tx, error=Timeout())
        client = tx._antom_refund_client()
        with patch.object(type(client.payment), 'inquiry_refund', return_value={
                'result': {'resultStatus': 'F', 'resultCode': 'ORDER_NOT_EXIST'}}):
            self.assertFalse(tx._alipay_query_and_record())
        with self.assertRaises(ValidationError):
            source._refund(1)
        self.assertEqual(tx.state, 'pending')

    def test_verified_rejection_releases_amount(self):
        source = self._source()
        tx = source._refund()
        self._poll(tx, {'result': {'resultStatus': 'F', 'resultMessage': 'Refund window exceeded'}})
        self._drain(tx)
        self.assertEqual((tx.state, tx.antom_refund_phase), ('error', 'failed'))
        self.assertEqual(source._refund(2).amount, -2)

    def test_mismatched_success_stays_pending(self):
        for field, value in [('refundRequestId', 'wrong'), ('paymentId', 'wrong'),
                             ('refundId', ''), ('refundAmount', {'currency': 'EUR', 'value': '200'}),
                             ('refundAmount', {'currency': 'USD', 'value': '201'})]:
            tx = self._source(reference='source-' + field + str(value))._refund(2)
            self._poll(tx, self._data(tx, **{field: value}))
            self.assertFalse(tx.payment_data_ids)
            self.assertEqual(tx.state, 'pending')

    def test_duplicate_and_late_notifications_cannot_demote_success(self):
        tx = self._source()._refund(2)
        data = self._data(tx, notifyType='REFUND_RESULT')
        tx._antom_record_refund(data, 'notify')
        self._drain(tx)
        tx._antom_record_refund(data, 'notify')
        self._drain(tx)
        tx._antom_record_refund(dict(data, refundStatus='FAIL', result={'resultStatus': 'F'}), 'notify')
        self._drain(tx)
        self.assertEqual((tx.state, tx.antom_refund_phase), ('done', 'done'))

    def test_zero_decimal_refund(self):
        tx = self._source(amount=100, currency_id=self._enable_currency('JPY').id)._refund(20)
        self.assertEqual(tx.antom_refund_payload['refundAmount'], {'currency': 'JPY', 'value': '20'})
        self._poll(tx, self._data(tx))
        self._drain(tx)
        self.assertEqual(tx.state, 'done')

    def test_merchant_and_environment_are_not_silently_switched(self):
        source = self._source()
        tx = source._refund(2)
        self.provider.is_live = True
        self.assertTrue(tx._antom_refund_client().sandbox)
        with self.assertRaises(ValidationError):
            source._refund(1)
        self.provider.antom_sandbox_client_id = 'different-merchant'
        with self.assertRaises(ValidationError):
            tx._antom_refund_client()

    def test_refund_identity_and_reservation_cannot_be_deleted(self):
        tx = self._source()._refund(2)
        with self.assertRaises(ValidationError):
            tx.with_context(payment_safe_write=True).write({'amount': -1})
        with self.assertRaises(ValidationError):
            tx.unlink()

    def test_real_rsa_signed_notification_and_tampering(self):
        tx = self._source()._refund(2)
        data = self._data(tx, notifyType='REFUND_RESULT')
        data.pop('paymentId')  # Not present in Antom notifyRefund contract.
        raw = json.dumps(data).encode()
        timestamp = '2026-09-11T12:00:00+08:00'
        message = b'POST /payment/alipay/refund/notify\nrefund-test.' + timestamp.encode() + b'.' + raw
        signature = quote(base64.b64encode(pkcs1_15.new(self.key).sign(SHA256.new(message))).decode())
        headers = {'client-id': 'refund-test', 'request-time': timestamp,
                   'signature': 'algorithm=RSA256,keyVersion=1,signature=' + signature}
        req = Mock(env=self.env)
        req.httprequest.headers = headers
        req.httprequest.get_json.return_value = data
        req.httprequest.get_data.return_value = raw
        with patch('odoo.addons.qing_payment_alipay.controllers.controllers.request', req):
            Alipay.alipay_refund_notify.__wrapped__(Alipay())
            self.assertEqual(len(tx.payment_data_ids), 1)
            self._drain(tx)
            req.httprequest.get_data.return_value = raw + b' '
            Alipay.alipay_refund_notify.__wrapped__(Alipay())
        self.assertEqual(tx.state, 'done')
        self.assertFalse(tx.payment_data_ids)
        req.make_response.assert_called_once_with('failed', status=400)

    def test_unsigned_or_wrong_merchant_notification_is_rejected(self):
        tx = self._source()._refund(2)
        req = Mock(env=self.env)
        req.httprequest.headers = {'client-id': 'another-merchant'}
        req.httprequest.get_json.return_value = self._data(tx, notifyType='REFUND_RESULT')
        req.httprequest.get_data.return_value = b'{}'
        with patch('odoo.addons.qing_payment_alipay.controllers.controllers.request', req):
            Alipay.alipay_refund_notify.__wrapped__(Alipay())
        self.assertFalse(tx.payment_data_ids)
        req.make_response.assert_called_once_with('failed', status=400)

    def test_refund_action_preserves_native_access_checks(self):
        source = self._source()
        with self.assertRaises(AccessError):
            source.with_user(self.env.ref('base.public_user')).action_refund(1)

    def test_refund_button_calls_native_global_refund(self):
        source = self._source()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No real refund')):
            source.action_refund()
        refund = source.child_transaction_ids.filtered(lambda tx: tx.operation == 'refund')
        self.assertEqual(refund.amount, -source.amount)
        self.assertEqual(refund.antom_refund_phase, 'queued')

    def test_refund_cron_and_payment_queue(self):
        tx = self._source()._refund(2)
        client = tx._antom_refund_client()
        with patch.object(type(client.payment), 'refund', return_value=self._data(tx)) as send, \
             patch.object(type(self.env['ir.cron']), '_commit_progress', return_value=60):
            self.env['payment.transaction']._cron_antom_refund()
        send.assert_called_once()
        self.assertEqual(tx.state, 'pending')
        self._drain(tx)
        self.assertEqual(tx.state, 'done')

    def test_bad_configuration_keeps_unsent_request_queued(self):
        tx = self._source()._refund(2)
        self.provider.antom_sandbox_client_id = 'wrong'
        tx.with_context(payment_safe_write=True)._antom_poll_refund()
        self.assertEqual(tx.antom_refund_phase, 'queued')
