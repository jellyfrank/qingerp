# -*- coding: utf-8 -*-

import json
import logging

from odoo import http
from odoo.http import request
from wechatpy.exceptions import InvalidSignatureException


_logger = logging.getLogger(__name__)


class WeworkIntelligentBotController(http.Controller):

    def _get_bot(self, callback_uuid):
        return request.env["wework.intelligent.bot"].sudo().search([
            ("active", "=", True),
            ("callback_uuid", "=", callback_uuid),
        ], limit=1)

    def _callback_response(self, payload, status=200):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        return request.make_response(
            payload,
            headers=[("Content-Type", "text/plain; charset=utf-8")],
            status=status,
        )

    @http.route(
        "/qing_wework/intelligent_bot/<string:callback_uuid>",
        auth="public",
        csrf=False,
        methods=["GET", "POST"],
        type="http",
    )
    def intelligent_bot_callback(self, callback_uuid, **kwargs):
        bot = self._get_bot(callback_uuid)
        if not bot:
            return request.make_response("Not Found", status=404)

        query_args = request.httprequest.args
        signature = query_args.get("msg_signature")
        timestamp = query_args.get("timestamp")
        nonce = query_args.get("nonce")
        try:
            crypto = bot._get_crypto()
            if request.httprequest.method == "GET":
                echo_str = query_args.get("echostr")
                if not all([signature, timestamp, nonce, echo_str]):
                    return request.make_response("Bad Request", status=400)
                return request.make_response(
                    crypto.verify_url(signature, timestamp, nonce, echo_str),
                )

            if not all([signature, timestamp, nonce]):
                return self._callback_response("missing signature", status=400)
            payload = crypto.decrypt_message(
                request.httprequest.get_data(as_text=True),
                signature,
                timestamp,
                nonce,
            )
            incoming_message = bot._record_incoming_message(payload)
            reply = bot._reply_to_callback(incoming_message, nonce, timestamp)
            return self._callback_response(reply or "success")
        except (InvalidSignatureException, ValueError, json.JSONDecodeError):
            _logger.warning("[QingERP WeCom] Intelligent bot callback signature validation failed.")
            return self._callback_response("invalid callback", status=403)
        except Exception:
            _logger.exception("[QingERP WeCom] Intelligent bot callback failed.")
            return self._callback_response("callback failed", status=500)
