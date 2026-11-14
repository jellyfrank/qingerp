import json
from unittest.mock import Mock, patch

from lxml import etree, html

from odoo.addons.payment.tests.common import PaymentCommon
from odoo.addons.payment import utils as payment_utils
from odoo.tests import tagged
from odoo.exceptions import ValidationError
from wechatpy.exceptions import WeChatPayException

from odoo.addons.qing_payment_wechatpay.controllers.controllers import WeChatPayController


@tagged("post_install", "-at_install")
class TestWeChatPayTransaction(PaymentCommon):
    def test_query_records_payment_and_processing_resets_post_processing(self):
        transaction = self._create_transaction("redirect", amount=12.34)
        transaction.with_context(payment_safe_write=True).write({"is_post_processed": True})
        response = {
            "return_code": "SUCCESS", "result_code": "SUCCESS", "trade_state": "SUCCESS",
            "out_trade_no": transaction._get_wechatpay_out_trade_no(),
            "transaction_id": "verified-payment", "total_fee": 1234, "fee_type": "CNY",
        }
        client = Mock()
        client.order.query.return_value = response
        with patch.object(type(self.provider), "_get_wechatpay", return_value=client):
            self.assertTrue(transaction._validate_wechatpay_tx())
        data = self.env["payment.data"].search([("transaction_id", "=", transaction.id)])
        self.assertEqual(len(data), 1)
        transaction.with_context(payment_safe_write=True)._process(data.payload)
        self.assertEqual(transaction.state, "done")
        self.assertFalse(transaction.is_post_processed)

    def test_query_missing_order_does_not_block_new_payment(self):
        transaction = self._create_transaction("redirect")
        client = Mock()
        client.order.query.side_effect = WeChatPayException("SUCCESS", errcode="ORDERNOTEXIST")
        with patch.object(type(self.provider), "_get_wechatpay", return_value=client):
            self.assertFalse(transaction._validate_wechatpay_tx())
        self.assertEqual(transaction.state, "draft")

    def test_missing_amount_and_currency_mismatch_are_not_accepted(self):
        for index, values in enumerate(({"fee_type": "CNY"}, {"total_fee": 1234, "fee_type": "USD"})):
            transaction = self._create_transaction("redirect", amount=12.34, reference=f"INVALID-{index}")
            transaction.with_context(payment_safe_write=True)._process({
                "return_code": "SUCCESS", "result_code": "SUCCESS", **values,
            })
            self.assertEqual(transaction.state, "error")

    def test_non_cny_order_is_rejected_before_creation(self):
        transaction = self._create_transaction("redirect", currency_id=self._enable_currency("USD").id)
        with self.assertRaises(ValidationError):
            transaction._wechatpay_check_currency()

    def test_paid_cashier_refresh_redirects_without_creating_order(self):
        transaction = self._create_transaction("redirect")
        controller = WeChatPayController()
        with patch.object(controller, "_find_transaction", return_value=transaction), \
             patch.object(type(transaction), "_validate_wechatpay_tx", return_value=True), \
             patch.object(type(self.provider), "_get_wechatpay_qrcode_url") as create, \
             patch("odoo.addons.qing_payment_wechatpay.controllers.controllers.request", new=Mock()) as request:
            controller.index.__wrapped__(controller, reference=transaction.reference)
        create.assert_not_called()
        request.redirect.assert_called_once_with("/payment/status")

    def test_notification_uses_transaction_provider_for_shared_merchant(self):
        self.provider.write({"wechatpay_mch_id": "test-merchant", "wechatpay_appid": "test-app"})
        transaction = self._create_transaction("redirect")
        payload = (
            f"<xml><mch_id>test-merchant</mch_id><appid>test-app</appid>"
            f"<out_trade_no>{transaction._get_wechatpay_out_trade_no()}</out_trade_no></xml>"
        ).encode()
        with patch("odoo.addons.qing_payment_wechatpay.controllers.controllers.request", new=Mock()) as request, \
             patch.object(type(self.provider), "search", side_effect=AssertionError("Do not select an arbitrary merchant provider")):
            request.env = self.env
            self.assertEqual(WeChatPayController()._get_provider_from_payload(payload), self.provider)

    def test_cashier_renders_order_amount_and_polling_credentials(self):
        view = self.env.ref("qing_payment_wechatpay.wechatpay_payform")
        arch = etree.fromstring(view.arch_db.encode())
        card = arch.xpath("//span[@class='wechatpay_order']/ancestor::div[contains(@class, 'card-body')]")[0]
        rendered = self.env['ir.qweb']._render(card, {
            'order': 'WX/REGRESSION/1', 'amount': 12.34, 'access_token': 'test-token',
        })
        document = html.fromstring(str(rendered))
        order = document.xpath("//span[@class='wechatpay_order']")[0]
        self.assertEqual(order.text, 'WX/REGRESSION/1')
        self.assertEqual(order.get('data-order'), 'WX/REGRESSION/1')
        self.assertEqual(order.get('data-access-token'), 'test-token')
        self.assertIn('12.34', document.text_content())

    def test_jsapi_parameters_survive_html_escaping(self):
        view = self.env.ref("qing_payment_wechatpay.wechatpay_jsapi_payform")
        arch = etree.fromstring(view.arch_db.encode())
        node = arch.xpath("//*[@id='wechatpay_jsapi_params']")[0]
        params = {'package': 'prepay_id=test', 'paySign': 'test<&"signature'}
        rendered = self.env['ir.qweb']._render(node, {'jsapi_params': json.dumps(params)})
        document = html.fromstring(str(rendered))
        self.assertEqual(json.loads(document.get('data-params')), params)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.enable_process_patcher = False
        cls.provider = cls._prepare_provider("wechatpay")
        cls.payment_method = cls.env.ref(
            "qing_payment_wechatpay.payment_method_wechatpay"
        )
        cls.payment_method_id = cls.payment_method.id
        cls.currency = cls._enable_currency("CNY")

    def test_success_notification_marks_transaction_done(self):
        transaction = self._create_transaction(
            "redirect",
            amount=12.34,
            reference="WX/ORDER/0001",
        )

        transaction.with_context(payment_safe_write=True)._apply_updates({
            "return_code": "SUCCESS",
            "result_code": "SUCCESS",
            "trade_state": "SUCCESS",
            "transaction_id": "wechat-transaction-1",
        })

        self.assertEqual(transaction.state, "done")
        self.assertEqual(transaction.provider_reference, "wechat-transaction-1")

    def test_extract_amount_data_uses_minor_currency_units(self):
        transaction = self._create_transaction("redirect")

        amount_data = transaction._extract_amount_data({
            "total_fee": "1234",
            "fee_type": "CNY",
        })

        self.assertEqual(amount_data, {
            "amount": 12.34,
            "currency_code": "CNY",
            "precision_digits": 2,
        })

    def test_rendering_values_include_valid_access_token(self):
        transaction = self._create_transaction("redirect")

        rendering_values = transaction._get_specific_rendering_values({})
        expected_token = payment_utils.generate_access_token(
            transaction.partner_id.id,
            transaction.amount,
            transaction.currency_id.id,
            env=self.env,
        )

        self.assertEqual(rendering_values["access_token"], expected_token)

    def test_search_by_reference_accepts_wechat_normalized_reference(self):
        transaction = self._create_transaction(
            "redirect",
            reference="WX/ORDER/0002",
        )

        matched_transaction = self.env["payment.transaction"]._search_by_reference(
            "wechatpay",
            {"out_trade_no": "WX-ORDER-0002"},
        )

        self.assertEqual(matched_transaction, transaction)

    def test_failed_notification_marks_transaction_error(self):
        transaction = self._create_transaction("redirect")

        transaction.with_context(payment_safe_write=True)._apply_updates({
            "return_code": "FAIL",
            "return_msg": "INVALID SIGN",
        })

        self.assertEqual(transaction.state, "error")
        self.assertIn("INVALID SIGN", transaction.state_message)
