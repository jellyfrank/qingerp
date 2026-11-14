import logging
from urllib.parse import quote

from wechatpy.exceptions import WeChatPayException

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

from odoo.addons.payment import utils as payment_utils


_logger = logging.getLogger(__name__)

WECHATPAY_CANCELED_STATES = {"CLOSED", "REVOKED"}
WECHATPAY_PENDING_STATES = {"NOTPAY", "USERPAYING"}


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _apply_updates(self, payment_data):
        if self.provider_code != "wechatpay":
            return super()._apply_updates(payment_data)

        return_code = payment_data.get("return_code")
        result_code = payment_data.get("result_code")
        if return_code != "SUCCESS" or result_code != "SUCCESS":
            message = (
                payment_data.get("err_code_des")
                or payment_data.get("return_msg")
                or _("WeChat Pay rejected the transaction.")
            )
            self._set_error(message)
            return

        provider_reference = payment_data.get("refund_id") or payment_data.get("transaction_id")
        if provider_reference:
            self.provider_reference = provider_reference

        if self.operation == "refund":
            self._set_done()
            self.env.ref("payment.cron_post_process_payment_tx")._trigger()
            return

        trade_state = payment_data.get("trade_state", "SUCCESS")
        if trade_state == "SUCCESS":
            self._set_done()
        elif trade_state in WECHATPAY_PENDING_STATES:
            self._set_pending()
        elif trade_state in WECHATPAY_CANCELED_STATES:
            self._set_canceled()
        else:
            self._set_error(
                payment_data.get("trade_state_desc")
                or _("WeChat Pay returned status %s.") % trade_state
            )

    def _extract_amount_data(self, payment_data):
        if self.provider_code != "wechatpay":
            return super()._extract_amount_data(payment_data)

        amount_field = "refund_fee" if self.operation == "refund" else "total_fee"
        amount_minor = payment_data.get(amount_field)
        if amount_minor in (None, ""):
            amount_minor = 0
        return {
            "amount": float(amount_minor) / 100,
            "currency_code": payment_data.get("fee_type") or "CNY",
            "precision_digits": 2,
        }

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        if provider_code != "wechatpay":
            return super()._extract_reference(provider_code, payment_data)
        return (
            payment_data.get("reference")
            or payment_data.get("out_refund_no")
            or payment_data.get("out_trade_no")
        )

    def _get_specific_rendering_values(self, processing_values):
        if self.provider_code != "wechatpay":
            return super()._get_specific_rendering_values(processing_values)
        access_token = payment_utils.generate_access_token(
            self.partner_id.id,
            self.amount,
            self.currency_id.id,
            env=self.env,
        )
        if self.is_mobile:
            return {
                "access_token": access_token,
                "api_url": "/shop/wechatpay/mobile",
                "amount": self.amount,
                "is_mobile": True,
                "reference": self.reference,
            }
        return {
            "access_token": access_token,
            "api_url": self.provider_id._wechatpay_get_api_url(),
            "amount": self.amount,
            "is_mobile": False,
            "reference": self.reference,
        }

    def _get_wechatpay_h5_values(self):
        self.ensure_one()
        self._wechatpay_check_currency()
        amount = int(round(self.amount * 100))
        out_trade_no = self._get_wechatpay_out_trade_no()
        try:
            response = self.provider_id._get_wechatpay().order.create(
                trade_type="MWEB",
                body=out_trade_no,
                out_trade_no=out_trade_no,
                total_fee=amount,
                notify_url=self.provider_id._get_wechatpay_notify_url(),
            )
        except WeChatPayException as error:
            _logger.exception("WeChat Pay failed to create an H5 order.")
            raise AccessError(_("Failed to create WeChat Pay payment: %s") % error) from error
        if response.get("return_code") != "SUCCESS" or response.get("result_code") != "SUCCESS":
            raise ValidationError(
                response.get("err_code_des")
                or response.get("return_msg")
                or _("Failed to create WeChat Pay payment.")
            )
        redirect_url = quote(f"{self.get_base_url()}/payment/status", safe="")
        return {"api_url": f"{response['mweb_url']}&redirect_url={redirect_url}"}

    def _get_wechatpay_jsapi_values(self, openid):
        self.ensure_one()
        self._wechatpay_check_currency()
        if not openid:
            raise ValidationError(_("WeChat Pay JSAPI requires an OpenID."))
        amount = int(round(self.amount * 100))
        out_trade_no = self._get_wechatpay_out_trade_no()
        try:
            response = self.provider_id._get_wechatpay().order.create(
                trade_type="JSAPI",
                body=out_trade_no,
                out_trade_no=out_trade_no,
                total_fee=amount,
                notify_url=self.provider_id._get_wechatpay_notify_url(),
                user_id=openid,
            )
        except WeChatPayException as error:
            _logger.exception("WeChat Pay failed to create a JSAPI order.")
            raise AccessError(_("Failed to create WeChat Pay JSAPI payment: %s") % error) from error
        if response.get("return_code") != "SUCCESS" or response.get("result_code") != "SUCCESS":
            raise ValidationError(
                response.get("err_code_des")
                or response.get("return_msg")
                or _("Failed to create WeChat Pay JSAPI payment.")
            )
        return self.provider_id._get_wechatpay().jsapi.get_jsapi_params(response["prepay_id"])

    def _get_wechatpay_out_trade_no(self, reference=None):
        self.ensure_one()
        return (reference or self.reference).replace("/", "-")

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        if provider_code != "wechatpay":
            return super()._search_by_reference(provider_code, payment_data)
        reference = self._extract_reference(provider_code, payment_data)
        if not reference:
            return self
        candidates = [reference]
        normalized_reference = reference.replace("-", "/")
        if normalized_reference != reference:
            candidates.append(normalized_reference)
        domain = Domain("provider_code", "=", "wechatpay") & Domain(
            "reference", "in", candidates
        )
        transactions = self.search(domain, order="id desc")
        exact_match = transactions.filtered(lambda transaction: transaction.reference == reference)
        return exact_match[:1] or transactions[:1]

    def _send_refund_request(self):
        if self.provider_code != "wechatpay":
            return super()._send_refund_request()

        self.ensure_one()
        source_transaction = self.source_transaction_id
        total_fee = int(round(abs(source_transaction.amount) * 100))
        refund_fee = int(round(abs(self.amount) * 100))
        with self.provider_id._wechatpay_refund_client() as client:
            response = client.refund.apply(
                total_fee,
                refund_fee,
                out_refund_no=self._get_wechatpay_out_trade_no(),
                transaction_id=source_transaction.provider_reference,
            )
        if response.get("return_code") != "SUCCESS" or response.get("result_code") != "SUCCESS":
            raise ValidationError(
                response.get("err_code_des")
                or response.get("return_msg")
                or _("WeChat Pay rejected the refund.")
            )
        response.update({
            "fee_type": self.currency_id.name,
            "out_refund_no": self._get_wechatpay_out_trade_no(),
            "refund_fee": refund_fee,
            "total_fee": total_fee,
        })
        self._record(response)

    def _validate_wechatpay_tx(self):
        self.ensure_one()
        if self.provider_code != "wechatpay":
            return False
        if self.state == "done":
            return True
        if self.operation == "refund":
            return False
        try:
            response = self.provider_id._get_wechatpay().order.query(
                out_trade_no=self._get_wechatpay_out_trade_no()
            )
        except WeChatPayException as error:
            if error.errcode == "ORDERNOTEXIST":
                return False
            raise ValidationError(_("WeChat Pay order query failed: %s") % error) from error
        if (
            response.get("return_code") != "SUCCESS"
            or response.get("result_code") != "SUCCESS"
            or response.get("trade_state") != "SUCCESS"
        ):
            return False
        if response.get("out_trade_no") != self._get_wechatpay_out_trade_no():
            raise ValidationError(_("WeChat Pay returned a different order reference."))
        self._record(response)
        return True

    def _wechatpay_check_currency(self):
        self.ensure_one()
        if self.currency_id.name != "CNY":
            raise ValidationError(_("WeChat Pay requires a transaction in CNY. No currency conversion is performed."))

    wechatpay_openid = fields.Char("WeChat Pay OpenID", copy=False)

    def action_validate_from_wechat(self):
        self.ensure_one()
        received = self._validate_wechatpay_tx()
        message = _("Payment received; queued for amount verification and processing.") \
            if received else _("WeChat Pay has not confirmed payment for this transaction.")
        return self._qing_notify(message, title=_("WeChat Pay"))
