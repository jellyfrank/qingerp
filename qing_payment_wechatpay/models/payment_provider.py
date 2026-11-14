import logging
import os
import ssl
from contextlib import contextmanager
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

from dateutil import tz
from wechatpy import WeChatOAuth
from wechatpy.pay import WeChatPay

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.qing_payment_wechatpay import const


_logger = logging.getLogger(__name__)

MOBILE_PAYMENT_METHODS = [
    ("h5", "H5 (MWEB)"),
    ("jsapi", "JSAPI (WeChat In-App)"),
]


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    @api.depends("code")
    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        wechatpay_providers = self.filtered(lambda provider: provider.code == "wechatpay")
        wechatpay_providers.update({
            "support_express_checkout": False,
            "support_manual_capture": None,
            "support_refund": "partial",
            "support_tokenization": False,
        })

    def _get_default_payment_method_codes(self):
        default_codes = super()._get_default_payment_method_codes()
        if self.code != "wechatpay":
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

    def _get_order_date(self):
        tz_shanghai = tz.gettz("Asia/Shanghai")
        date_start = datetime.now().astimezone(tz_shanghai)
        date_end = (datetime.now() + timedelta(hours=2)).astimezone(tz_shanghai)
        return date_start, date_end

    def _get_supported_currencies(self):
        supported_currencies = super()._get_supported_currencies()
        if self.code == "wechatpay":
            return supported_currencies.filtered(
                lambda currency: currency.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    def _get_wechatpay(self, with_cert=False):
        self.ensure_one()
        kwargs = {}
        if with_cert:
            cert_path = self.wechatpay_mch_cert
            key_path = self.wechatpay_mch_key
            if not cert_path or not key_path:
                raise UserError(_("Merchant cert/key path is required for refunds."))
            if not os.path.isfile(cert_path) or not os.path.isfile(key_path):
                raise UserError(_("Merchant cert/key file does not exist. Please check file paths."))
            with open(cert_path, encoding="utf-8", errors="ignore") as cert_file:
                cert_content = cert_file.read()
            with open(key_path, encoding="utf-8", errors="ignore") as key_file:
                key_content = key_file.read()
            if "BEGIN CERTIFICATE" not in cert_content:
                raise UserError(_("Merchant cert must be in PEM format."))
            if "BEGIN PRIVATE KEY" not in key_content and "BEGIN RSA PRIVATE KEY" not in key_content:
                raise UserError(_("Merchant key must be in PEM format."))
            kwargs.update(mch_cert=cert_path, mch_key=key_path)

        try:
            return WeChatPay(
                self.wechatpay_appid,
                self.wechatpay_app_key,
                self.wechatpay_mch_id,
                **kwargs,
            )
        except Exception as error:
            _logger.exception("Failed to create the WeChat Pay client.")
            raise UserError(_("Failed to create the WeChat Pay client: %s") % error) from error

    def _get_wechatpay_app_secret(self):
        self.ensure_one()
        return self.wechatpay_app_secret or self.wechactpay_app_secret

    @contextmanager
    def _wechatpay_refund_client(self):
        """Keep uploaded PEM files alive only for the duration of the SDK request."""
        self.ensure_one()
        if not self.wechatpay_mch_cert_file and not self.wechatpay_mch_key_file:
            yield self._get_wechatpay(with_cert=True)
            return
        if not self.wechatpay_mch_cert_file or not self.wechatpay_mch_key_file:
            raise UserError(_("Upload both the merchant certificate and private key."))
        with TemporaryDirectory(prefix="wechatpay-") as directory:
            cert_path = os.path.join(directory, "apiclient_cert.pem")
            key_path = os.path.join(directory, "apiclient_key.pem")
            for path, value in (
                (cert_path, self.wechatpay_mch_cert_file),
                (key_path, self.wechatpay_mch_key_file),
            ):
                with open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as pem:
                    pem.write(value.content)
            try:
                ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_cert_chain(
                    cert_path, key_path, password="",
                )
            except (ssl.SSLError, ValueError) as error:
                raise UserError(_(
                    "Upload a valid PEM merchant certificate and its matching, unencrypted private key."
                )) from error
            client = self._get_wechatpay()
            client.mch_cert = cert_path
            client.mch_key = key_path
            try:
                yield client
            finally:
                client._http.close()

    def _get_wechatpay_oauth(self, redirect_url, state=None, scope="snsapi_base"):
        self.ensure_one()
        app_secret = self._get_wechatpay_app_secret()
        if not app_secret:
            raise UserError(_("WeChat Pay JSAPI requires an App Secret."))
        callback_url = f"{self.get_base_url()}{redirect_url}"
        return WeChatOAuth(
            self.wechatpay_appid,
            app_secret,
            callback_url,
            scope=scope,
            state=state or "",
        )

    def _get_wechatpay_qrcode_url(self, transaction):
        self.ensure_one()
        transaction.ensure_one()
        transaction._wechatpay_check_currency()
        amount = int(round(transaction.amount * 100))
        out_trade_no = transaction._get_wechatpay_out_trade_no()
        date_start, date_end = self._get_order_date()
        try:
            response = self._get_wechatpay().order.create(
                trade_type="NATIVE",
                body=out_trade_no,
                time_start=date_start,
                time_expire=date_end,
                out_trade_no=out_trade_no,
                total_fee=amount,
                notify_url=self._get_wechatpay_notify_url(),
            )
        except Exception as error:
            _logger.exception("WeChat Pay failed to create a Native order.")
            raise ValidationError(_("WeChat Pay failed to create the order: %s") % error) from error
        if response.get("return_code") != "SUCCESS" or response.get("result_code") != "SUCCESS":
            raise ValidationError(
                response.get("err_code_des")
                or response.get("return_msg")
                or _("WeChat Pay failed to create the order.")
            )
        return response["code_url"]

    def _get_wechatpay_notify_url(self):
        self.ensure_one()
        return f"{self.get_base_url()}/payment/wechatpay/notify"

    def _parse_wechatpay_notification(self, payload):
        self.ensure_one()
        try:
            return self._get_wechatpay().parse_payment_result(payload)
        except Exception as error:
            _logger.exception("Failed to verify the WeChat Pay notification.")
            raise ValidationError(_("Invalid WeChat Pay notification: %s") % error) from error

    def _wechatpay_get_api_url(self):
        self.ensure_one()
        return "/shop/wechatpay"

    code = fields.Selection(
        selection_add=[("wechatpay", "WeChat Pay")],
        ondelete={"wechatpay": "set default"},
    )
    wechactpay_app_secret = fields.Char("App Secret (Legacy)")
    wechatpay_app_key = fields.Char("API Key")
    wechatpay_app_secret = fields.Char("App Secret (OAuth)")
    wechatpay_appid = fields.Char("WeChat Pay AppID", size=32)
    wechatpay_mch_cert = fields.Char("Merchant Certificate File Path")
    wechatpay_mch_cert_file = fields.Binary(
        "Merchant Certificate", attachment=False, copy=False, groups="base.group_system",
    )
    wechatpay_mch_cert_filename = fields.Char("Certificate Filename", copy=False)
    wechatpay_mch_id = fields.Char("Merchant ID", size=32)
    wechatpay_mch_key = fields.Char("Merchant Private Key File Path")
    wechatpay_mch_key_file = fields.Binary(
        "Merchant Private Key", attachment=False, copy=False, groups="base.group_system",
    )
    wechatpay_mch_key_filename = fields.Char("Private Key Filename", copy=False)
    wechatpay_mobile_pay_method = fields.Selection(
        MOBILE_PAYMENT_METHODS,
        string="Mobile Payment Method",
        default="jsapi",
        required=True,
    )
