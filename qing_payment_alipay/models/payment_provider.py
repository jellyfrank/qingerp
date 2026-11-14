#!/usr/bin/python3
# @Time    : 2022-03-30
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import models, fields, SUPERUSER_ID, _, api
from alipay.api import AliPay, Antom
from Crypto.PublicKey import RSA
from odoo.addons.payment import utils as payment_utils
from odoo.addons.qing_payment_alipay import const
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

PAYMENT_METHODS = [(k, v) for k, v in Antom.list_payment_methods()]
LOCATIONS = [("as", "Asia"), ("na", "North America"), ("er", "Europe")]


class AlipayProvider(models.Model):
    _inherit = "payment.provider"

    def _alipay_read_key(self, value):
        """Read Odoo 20 BinaryValue content, without a second base64 decode."""
        if not value:
            raise UserError(_("Please upload the Alipay private and public keys."))
        try:
            return RSA.import_key(value.content)
        except (ValueError, TypeError, IndexError) as error:
            raise UserError(_("Invalid Alipay RSA key. Please upload a PEM key file.")) from error

    def _get_global_instance(self, sandbox=False):
        """
        Get the global instance of the payment provider.
        This method is used to get the global instance of the payment provider.

        :param sandbox: Boolean indicating whether to use the sandbox environment.
        :return: The global instance of the payment provider.
        """
        if not sandbox:
            client_id = self.antom_client_id
            private_key_file = self.antom_private_key
            public_key_file = self.antom_public_key
        else:
            client_id = self.antom_sandbox_client_id
            private_key_file = self.antom_sandbox_private_key
            public_key_file = self.antom_sandbox_public_key

        return Antom(
            client_id,
            self._alipay_read_key(private_key_file),
            public_key=self._alipay_read_key(public_key_file),
            sign_type=self.alipay_sign_type or 'rsa2',
            location=self.antom_sandbox_location if sandbox else self.antom_location,
            sandbox=sandbox,
        )

    def _get_local_instance(self, sandbox=False):
        """
        Get the local instance of the payment provider.
        This method is used to get the local instance of the payment provider.
        """
        if not sandbox:
            client_id = self.alipay_appid
            private_key_file = self.alipay_secret
            public_key_file = self.alipay_public_key
        else:
            client_id = self.alipay_sandbox_appid
            private_key_file = self.alipay_sandbox_secret
            public_key_file = self.alipay_sandbox_public_key

        return AliPay(
            client_id,
            self._alipay_read_key(private_key_file),
            public_key=self._alipay_read_key(public_key_file),
            sign_type=(self.alipay_sandbox_sign_type if sandbox else self.alipay_sign_type) or 'rsa2',
            sandbox=sandbox,
        )

    def _get_alipay(self):
        """
        Getting Alipay Client
        """
        is_sandbox = not self.is_live
        alipay = self._get_local_instance(is_sandbox) if not self.is_global else self._get_global_instance(is_sandbox)
        if not alipay:
            raise UserError(_("Failed to get Alipay client. Please check your configuration."))

        base_url = self.get_base_url().rstrip('/')
        alipay.return_url = f"{base_url}/payment/alipay/process"
        alipay.notify_url = f"{base_url}/payment/alipay/notify"
        return alipay

    def _get_global_api_url(self,global_client, amount, reference, currency="USD"):
        """
        Generate the global Alipay payment URL.
        This method generates the payment URL for Alipay based on the provided amount, reference, and currency.

        :param global_client: The Antom client instance for global payments.
        :param amount: The amount to be paid.
        :param reference: The reference for the payment.
        :param currency: The currency for the payment, default is "USD".
        :return: The payment URL.
        :raises Exception: If there is an error in generating the payment URL.
        """
        url = None
        self.ensure_one()
        currency_record = self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', currency)], limit=1,
        )
        amount = payment_utils.to_minor_currency_units(amount, currency_record)
        payment_method = (
            self.antom_payment_method
            if self.is_live
            else self.antom_sandbox_payment_method
        )
        data = {
            "order": {
                "orderAmount": {"value": int(amount), "currency": currency},
                "referenceOrderId": reference,
                "orderDescription": reference,
            },
            "paymentAmount": {"value": int(amount), "currency": currency},
            "paymentMethod": {"paymentMethodType": payment_method},
            "paymentNotifyUrl": global_client.notify_url,
            "paymentRedirectUrl": global_client.return_url,
            "paymentRequestId": reference,
            "productCode": "CASHIER_PAYMENT",
            "settlementStrategy": {"settlementCurrency": "USD"},
            "env": {"terminalType": "WEB"},
        }
        res = global_client.payment.pay(**data)
        _logger.debug(f"[Qing Payment Alipay]:global resp:{res}")
        if res["result"]["resultStatus"] == "F":
            msg = f"Sorry, something went wrong, please try again later.\nDetail: {res['result']['resultMessage']}"
            raise Exception(msg)
        url = res.get("redirectActionForm", {}).get("redirectUrl")
        if not url:
            raise UserError(_("Antom did not return a checkout URL. Please check the payment configuration."))
        return url

    def _get_china_api_url(self, china_client, amount, reference):
        """
        Generate the China Alipay payment URL.
        This method generates the payment URL for Alipay based on the provided amount, reference, and currency.

        :param amount: The amount to be paid.
        :param reference: The reference for the payment.
        :return: The payment URL.
        """
        self.ensure_one()
        return china_client.pay.trade_page_pay(
            reference, amount, reference, product_code="FAST_INSTANT_TRADE_PAY"
        )

    def _alipay_get_api_url(self, amount, reference, currency="CNY"):
        """
        Generate the Alipay payment URL.
        This method generates the payment URL for Alipay based on the provided amount, reference, and currency.

        :param amount: The amount to be paid.
        :param reference: The reference for the payment.
        :param currency: The currency for the payment, default is "CNY".
        :return: The payment URL.
        :raises UserError: If there is an error in generating the payment URL.
        """
        self.ensure_one()
        alipay = self._get_alipay()
        if self.is_global:
            return self._get_global_api_url(alipay, amount, reference, currency)

        if currency != 'CNY':
            raise UserError(_("Mainland Alipay requires a CNY transaction."))
        return self._get_china_api_url(alipay, amount, reference)

    def _get_supported_currencies(self):
        """
        Override of `payment` to return the supported currencies.
        """
        supported_currencies = super()._get_supported_currencies()
        if self.code != "alipay":
            return supported_currencies
        return supported_currencies.filtered(
            lambda c: c.name in (const.SUPPORTED_CURRENCIES if self.is_global else ('CNY',))
        )

    @api.depends('code', 'is_global')
    def _compute_available_currency_ids(self):
        super()._compute_available_currency_ids()

    def _get_default_payment_method_codes(self):
        """
        Override of `payment` to return the default payment method codes.
        """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != "alipay":
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

    def _validate_payment_data(self, data):
        """
        Validate the payment data received from Alipay.
        This method checks the signature, app ID, seller ID, and transaction amount.

        :param data: The payment data received from Alipay.
        :return: True if the validation is successful, False otherwise.
        :raises UserError: If the validation fails.
        """
        self.ensure_one()
        result = False
        alipay = self._get_alipay()
        # Validate the signature
        try:
            valid = alipay.comm.validate_sign(dict(data))
        except Exception:
            valid = False  # SDK raises for missing or malformed signatures.
        if not valid:
            _logger.warning("Rejected Alipay notification with invalid signature.")
            return result
        # Validate the app ID and seller ID
        app_id = self.alipay_appid if self.is_live else self.alipay_sandbox_appid
        seller_id = self.seller_id if self.is_live else self.sandbox_seller_id
        if not app_id or app_id != data.get("app_id"):
            _logger.warning("Rejected Alipay notification with mismatched application.")
            return result

        if not seller_id or seller_id != data.get("seller_id"):
            _logger.warning("Rejected Alipay notification with mismatched seller.")
            return result

        return True

    def _validate_trasnaction(self, data):
        """
        Validate the transaction data received from Alipay.
        This method checks the transaction data and verifies the payment.

        :param data: The transaction data received from Alipay.
        :return: True if the validation is successful, False otherwise.
        :raises UserError: If the validation fails.
        """
        self.ensure_one()
        transaction_obj = self.env["payment.transaction"].sudo()
        domain = [('reference', '=', data.get("out_trade_no")), ('provider_id', '=', self.id)]
        transaction = transaction_obj.search(domain, limit=1)

        if not transaction:
            return False
        transaction._record(data)

        return True


    def _verify_pay(self, data):
        """
        Verify the payment data received from Alipay.
        This method checks the signature, app ID, seller ID, and transaction amount.

        :param data: The payment data received from Alipay.
        :return: True if the verification is successful, False otherwise.
        :raises UserError: If the verification fails.
        """
        if not self._validate_payment_data(data):
            return False

        if not self.env.user:
            self = self.with_user(SUPERUSER_ID)

        return self._validate_trasnaction(data)

    @api.depends('code', 'is_global')
    def _compute_feature_support_fields(self):
        """
        Override of `payment` to compute the feature support fields.
        This method computes the feature support fields for Alipay.
        """
        super()._compute_feature_support_fields()
        for provider in self:
            if provider.code != "alipay":
                continue
            provider.update({
                'support_refund': 'partial',
            })


    alipay_appid = fields.Char("Alipay AppId")
    alipay_secret = fields.Binary("Alipay Merchant Private Key")
    alipay_public_key = fields.Binary("Alipay Public Key")
    alipay_sign_type = fields.Selection(
        selection=[("rsa", "RSA"), ("rsa2", "RSA2")], string="Sign Type", default="rsa2"
    )
    alipay_sandbox_appid = fields.Char("Alipay SandBox AppId")
    alipay_sandbox_secret = fields.Binary("Alipay Sandbox Merchant Private Key")
    alipay_sandbox_public_key = fields.Binary("Alipay SandBox Public Key")
    alipay_sandbox_sign_type = fields.Selection(
        selection=[("rsa", "RSA"), ("rsa2", "RSA2")], string="SandBox Sign Type"
    )
    code = fields.Selection(
        selection_add=[("alipay", "AliPay")], ondelete={"alipay": "set default"}
    )
    is_global = fields.Boolean("Global", default=False)
    seller_id = fields.Char("Alipay Seller Id")
    sandbox_seller_id = fields.Char("Alipay SandBox Seller Id")
    antom_client_id = fields.Char("Client ID")
    antom_private_key = fields.Binary("Private Key")
    antom_public_key = fields.Binary("Public Key")
    antom_location = fields.Selection(LOCATIONS, string="Location", default="as")
    antom_payment_method = fields.Selection(
        PAYMENT_METHODS, string="Payment Method", default="CONNECT_WALLET"
    )

    antom_sandbox_client_id = fields.Char("Sandbox Client ID")
    antom_sandbox_private_key = fields.Binary("Sandbox Private Key")
    antom_sandbox_public_key = fields.Binary("Sandbox Public Key")
    antom_sandbox_location = fields.Selection(
        LOCATIONS, string="Sandbox Location", default="as"
    )
    antom_sandbox_payment_method = fields.Selection(
        PAYMENT_METHODS, string="Sandbox Payment Method", default="CONNECT_WALLET"
    )
