#!/usr/bin/python3
# @Time    : 2025-08-14
# @Author  : Kevin Kong (kfx2007@163.com)

import logging

from odoo import _
from odoo.addons.payment.controllers.portal import PaymentPortal
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentPortal(PaymentPortal):

    def _create_transaction(
        self,
        provider_id,
        payment_method_id,
        token_id,
        amount,
        currency_id,
        partner_id,
        flow,
        tokenization_requested,
        landing_route,
        reference_prefix=None,
        is_validation=False,
        custom_create_values=None,
        **kwargs,
    ):
        """Override to add custom logic for transaction creation."""
        custom_create_values = dict(custom_create_values or {})
        if kwargs.get("mobile"):
            custom_create_values["is_mobile"] = True

        return super()._create_transaction(
            provider_id=provider_id,
            payment_method_id=payment_method_id,
            token_id=token_id,
            amount=amount,
            currency_id=currency_id,
            partner_id=partner_id,
            flow=flow,
            tokenization_requested=tokenization_requested,
            landing_route=landing_route,
            reference_prefix=reference_prefix,
            is_validation=is_validation,
            custom_create_values=custom_create_values,
            **kwargs,
        )

    @staticmethod
    def _validate_transaction_kwargs(kwargs, additional_allowed_keys=()):
        whitelist = {
            "provider_id",
            "payment_method_id",
            "token_id",
            "amount",
            "flow",
            "tokenization_requested",
            "landing_route",
            "is_validation",
            "csrf_token",
            "mobile",
        }
        whitelist.update(additional_allowed_keys)
        rejected_keys = set(kwargs.keys()) - whitelist
        if rejected_keys:
            raise ValidationError(
                _("The following kwargs are not whitelisted: %s", ", ".join(rejected_keys))
            )
