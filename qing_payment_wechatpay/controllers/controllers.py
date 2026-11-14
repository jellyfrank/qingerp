import base64
import json
import logging
from io import BytesIO
from urllib.parse import urlencode
from xml.etree import ElementTree

import qrcode

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.http import request

from odoo.addons.payment import utils as payment_utils


_logger = logging.getLogger(__name__)

WECHATPAY_FAILURE_RESPONSE = (
    b"<xml><return_code><![CDATA[FAIL]]></return_code>"
    b"<return_msg><![CDATA[VERIFY ERROR]]></return_msg></xml>"
)
WECHATPAY_SUCCESS_RESPONSE = (
    b"<xml><return_code><![CDATA[SUCCESS]]></return_code>"
    b"<return_msg><![CDATA[OK]]></return_msg></xml>"
)


class WeChatPayController(http.Controller):
    _return_url = "/payment/wechatpay/validate"

    def _find_transaction(self, reference, access_token=None, validate_access=False):
        transaction_model = request.env["payment.transaction"].sudo()
        payment_data = {"reference": reference}
        transaction = transaction_model._search_by_reference("wechatpay", payment_data)
        if validate_access and transaction and not payment_utils.check_access_token(
            access_token,
            transaction.partner_id.id,
            transaction.amount,
            transaction.currency_id.id,
        ):
            _logger.warning("Rejected access to WeChat Pay transaction %s.", reference)
            return transaction_model
        return transaction

    def _get_provider_from_payload(self, payload):
        values = self._parse_xml_payload(payload)
        transaction = request.env["payment.transaction"].sudo()._search_by_reference(
            "wechatpay", values,
        )
        if transaction:
            provider = transaction.provider_id
            if (
                values.get("mch_id") == provider.wechatpay_mch_id
                and values.get("appid") == provider.wechatpay_appid
            ):
                return provider
            return request.env["payment.provider"]
        merchant_id = values.get("mch_id")
        app_id = values.get("appid")
        if not merchant_id:
            return request.env["payment.provider"]
        domain = Domain("code", "=", "wechatpay")
        domain &= Domain("active", "=", True)
        domain &= Domain("wechatpay_mch_id", "=", merchant_id)
        if app_id:
            domain &= Domain("wechatpay_appid", "=", app_id)
        provider_model = request.env["payment.provider"].sudo()
        return provider_model.search(domain, limit=1)

    def _is_wechat_browser(self):
        user_agent = request.httprequest.headers.get("User-Agent", "").lower()
        return "micromessenger" in user_agent

    def _make_qrcode(self, value):
        image = qrcode.make(value)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded_image = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded_image}"

    def _parse_xml_payload(self, payload):
        if not payload:
            return {}
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            _logger.warning("Received malformed WeChat Pay XML.")
            return {}
        return {child.tag: child.text or "" for child in root}

    def _render_jsapi_payment_page(self, transaction, openid):
        transaction.ensure_one()
        transaction.with_context(payment_safe_write=True).write({"wechatpay_openid": openid})
        jsapi_values = transaction._get_wechatpay_jsapi_values(openid)
        values = {
            "amount": transaction.amount,
            "jsapi_params": json.dumps(jsapi_values, ensure_ascii=False),
            "order": transaction.reference,
        }
        return request.render("qing_payment_wechatpay.wechatpay_jsapi_payform", values)

    @http.route("/shop/wechatpay", type="http", auth="public", website=True)
    def index(self, reference=None, access_token=None, **kwargs):
        transaction = self._find_transaction(reference, access_token, validate_access=True)
        if not transaction:
            values = {"error": _("WeChat Pay transaction was not found.")}
            return request.render("qing_payment_wechatpay.wechatpay_payform", values)
        if transaction._validate_wechatpay_tx():
            return request.redirect("/payment/status")
        qrcode_url = transaction.provider_id._get_wechatpay_qrcode_url(transaction)
        values = {
            "access_token": access_token,
            "amount": transaction.amount,
            "order": transaction.reference,
            "qrcode": self._make_qrcode(qrcode_url),
        }
        return request.render("qing_payment_wechatpay.wechatpay_payform", values)

    @http.route("/shop/wechatpay/mobile", type="http", auth="public", website=True)
    def mobile(self, reference=None, access_token=None, code=None, state=None, **kwargs):
        transaction = self._find_transaction(
            reference or state,
            access_token,
            validate_access=True,
        )
        if not transaction:
            return request.not_found()
        if transaction._validate_wechatpay_tx():
            return request.redirect("/payment/status")

        provider = transaction.provider_id
        if provider.wechatpay_mobile_pay_method == "jsapi":
            if not self._is_wechat_browser() and not code:
                values = {"error": _("Open this payment page inside WeChat to use JSAPI.")}
                return request.render("qing_payment_wechatpay.wechatpay_payform", values)

            if not code and transaction.wechatpay_openid:
                return self._render_jsapi_payment_page(transaction, transaction.wechatpay_openid)
            callback_query = urlencode({
                "access_token": access_token,
                "reference": transaction.reference,
            })
            oauth = provider._get_wechatpay_oauth(
                f"/shop/wechatpay/mobile?{callback_query}",
                state=transaction.reference,
            )
            if not code:
                return request.redirect(oauth.authorize_url, local=False)
            try:
                oauth.fetch_access_token(code)
            except Exception:
                _logger.exception(
                    "Failed to fetch the WeChat OAuth token for transaction %s.",
                    transaction.reference,
                )
                values = {"error": _("WeChat authorization failed. Please try again.")}
                return request.render("qing_payment_wechatpay.wechatpay_payform", values)
            return self._render_jsapi_payment_page(transaction, oauth.open_id)

        h5_values = transaction._get_wechatpay_h5_values()
        return request.redirect(h5_values["api_url"], local=False)

    @http.route("/shop/wechatpay/result", type="http", auth="public", website=True)
    def wechatpay_query(self, order=None, access_token=None):
        transaction = self._find_transaction(order, access_token, validate_access=True)
        if not transaction or not transaction._validate_wechatpay_tx():
            return request.make_json_response({"order": order, "result": 1})
        return request.make_json_response({"order": order, "result": 0})

    @http.route(_return_url, type="http", auth="none", methods=["GET", "POST"], csrf=False)
    def wechatpay_validate(self, order=None, access_token=None, **kwargs):
        transaction = self._find_transaction(order, access_token, validate_access=True)
        if transaction:
            transaction._validate_wechatpay_tx()
        return request.redirect("/payment/status")

    @http.route(
        "/payment/wechatpay/notify",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def wechatpay_notify(self, **kwargs):
        payload = request.httprequest.data
        provider = self._get_provider_from_payload(payload)
        if not provider:
            _logger.warning("No WeChat Pay provider matches the notification merchant.")
            return request.make_response(WECHATPAY_FAILURE_RESPONSE, headers=[("Content-Type", "text/xml")])
        try:
            payment_data = provider._parse_wechatpay_notification(payload)
        except ValidationError:
            return request.make_response(WECHATPAY_FAILURE_RESPONSE, headers=[("Content-Type", "text/xml")])

        transaction_model = request.env["payment.transaction"].sudo()
        transaction = transaction_model._search_by_reference("wechatpay", payment_data)
        if not transaction or transaction.provider_id != provider:
            _logger.warning("No transaction matches the verified WeChat Pay notification.")
            return request.make_response(WECHATPAY_FAILURE_RESPONSE, headers=[("Content-Type", "text/xml")])
        transaction._record(payment_data)
        return request.make_response(WECHATPAY_SUCCESS_RESPONSE, headers=[("Content-Type", "text/xml")])
