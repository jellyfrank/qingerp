# -*- coding: utf-8 -*-

import base64
import hashlib
import json
import logging
import secrets
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from ...tools.actions import notification_action
from psycopg2 import IntegrityError
from wechatpy.work.ai_bot import WeChatAIBotCrypto, WeChatAIBotWebSocketClient

from ...services import WeworkIntelligentBotWebSocketManager


CONNECTION_MODES = [
    ("callback", "URL Callback"),
    ("websocket", "WebSocket Long Connection"),
]

WEBSOCKET_STATES = [
    ("stopped", "Stopped"),
    ("connecting", "Connecting"),
    ("connected", "Connected"),
    ("error", "Error"),
]

_logger = logging.getLogger(__name__)


class WeworkIntelligentBot(models.Model):
    _name = "wework.intelligent.bot"
    _description = "WeCom Intelligent Bot"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    @api.depends("callback_uuid")
    def _compute_callback_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_str(
            "web.base.url",
            default="",
        )
        for bot in self:
            bot.callback_url = "%s/qing_wework/intelligent_bot/%s" % (
                base_url.rstrip("/"),
                bot.callback_uuid,
            ) if base_url and bot.callback_uuid else False

    @api.depends("message_ids")
    def _compute_message_count(self):
        for bot in self:
            bot.message_count = len(bot.message_ids)

    @api.depends("media_message_ids")
    def _compute_media_message_count(self):
        for bot in self:
            bot.media_message_count = len(bot.media_message_ids)

    def _generate_encoding_aes_key(self):
        """Return a 43-character standard Base64 key required by WeCom."""
        return base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")

    def _get_crypto(self):
        self.ensure_one()
        if not self.callback_token or not self.encoding_aes_key:
            raise UserError(_("Please generate the intelligent bot callback credentials first."))
        return WeChatAIBotCrypto(self.callback_token, self.encoding_aes_key)

    def _get_external_message_id(self, payload):
        external_message_id = (
            payload.get("msgid")
            or payload.get("msg_id")
            or payload.get("message_id")
        )
        if external_message_id:
            return str(external_message_id)
        serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(serialized_payload.encode()).hexdigest()

    def _get_stream_id(self, payload):
        stream = payload.get("stream") or {}
        return stream.get("id") if isinstance(stream, dict) else False

    def _is_websocket_connection_enabled(self):
        self.ensure_one()
        return bool(
            self.active
            and self.connection_mode == "websocket"
            and self.websocket_enabled
            and self.websocket_bot_id
            and self.websocket_secret
        )

    def _log_outbound_payload(self, incoming_message, payload, content=False, stream_id=False):
        self.ensure_one()
        self.env["wework.intelligent.bot.message"].sudo().create({
            "bot_id": self.id,
            "content": content,
            "conversation_id": incoming_message.conversation_id,
            "conversation_type": incoming_message.conversation_type,
            "direction": "outbound",
            "message_type": payload["msgtype"],
            "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "sent_at": fields.Datetime.now(),
            "state": "sent",
            "stream_id": stream_id,
        })

    def _prepare_websocket_reply(self, incoming_message):
        self.ensure_one()
        content = (self.reply_message or "").strip()
        if not content:
            return False
        if incoming_message.message_type in ("text", "image", "mixed"):
            stream_id = uuid.uuid4().hex
            payload = {
                "msgtype": "stream",
                "stream": {
                    "content": content,
                    "finish": True,
                    "id": stream_id,
                },
            }
            self._log_outbound_payload(incoming_message, payload, content, stream_id)
            return payload
        payload = {
            "msgtype": "text",
            "text": {"content": content},
        }
        self._log_outbound_payload(incoming_message, payload, content)
        return payload

    def _mark_websocket_reply_delivered(self, incoming_message, payload):
        """Allow optional modules to persist a successful WebSocket reply."""
        return False

    def _deliver_websocket_pending_messages(self, client):
        self.ensure_one()
        MediaMessage = self.env["wework.intelligent.bot.media.message"].sudo()
        messages = MediaMessage._claim_pending_websocket_messages(self.id)
        for message in messages:
            try:
                message._send_websocket(client)
            except Exception as error:
                _logger.exception("[QingERP WeCom] Intelligent bot media %s failed", message.id)
                message._mark_retry(error)
        return len(messages)

    def _record_incoming_message(self, payload):
        self.ensure_one()
        external_message_id = self._get_external_message_id(payload)
        Message = self.env["wework.intelligent.bot.message"].sudo()
        message_domain = [
            ("bot_id", "=", self.id),
            ("external_message_id", "=", external_message_id),
        ]
        existing_message = Message.search(message_domain, limit=1)
        if existing_message:
            return existing_message

        message_type = payload.get("msgtype") or "event"
        message_body = payload.get(message_type) or {}
        content = message_body.get("content") if isinstance(message_body, dict) else False
        sender = payload.get("from") or {}
        logged_payload = dict(payload)
        logged_payload.pop("response_url", None)
        chat_type = payload.get("chattype") or "single"
        conversation_id = payload.get("chatid") if chat_type == "group" else sender.get("userid")
        values = {
            "bot_id": self.id,
            "content": content or False,
            "conversation_id": conversation_id or False,
            "conversation_type": "group" if chat_type == "group" else "single",
            "direction": "inbound",
            "external_message_id": external_message_id,
            "message_type": message_type,
            "payload": json.dumps(logged_payload, ensure_ascii=False, sort_keys=True),
            "received_at": fields.Datetime.now(),
            "response_url": payload.get("response_url") or False,
            "sender_user_id": sender.get("userid") or False,
            "state": "received",
            "stream_id": self._get_stream_id(payload),
        }
        try:
            with self.env.cr.savepoint():
                return Message.create(values)
        except IntegrityError:
            return Message.search(message_domain, limit=1)

    def _reply_to_callback(self, incoming_message, nonce, timestamp):
        self.ensure_one()
        content = (self.reply_message or "").strip()
        if not content:
            return False

        if incoming_message.message_type == "stream":
            if not incoming_message.stream_id:
                return False
            payload = {
                "msgtype": "stream",
                "stream": {
                    "content": content,
                    "finish": True,
                    "id": incoming_message.stream_id,
                },
            }
            message_content = content
            stream_id = incoming_message.stream_id
        elif incoming_message.message_type in ("text", "image", "mixed"):
            stream_id = uuid.uuid4().hex
            payload = {
                "msgtype": "stream",
                "stream": {
                    "content": "",
                    "finish": False,
                    "id": stream_id,
                },
            }
            message_content = False
        else:
            payload = {
                "msgtype": "text",
                "text": {"content": content},
            }
            message_content = content
            stream_id = False

        self._log_outbound_payload(incoming_message, payload, message_content, stream_id)
        return self._get_crypto().encrypt_message(payload, nonce, timestamp)

    active = fields.Boolean(default=True)
    callback_token = fields.Char(
        copy=False,
        groups="qing_wework.group_wework_manager",
        string="Callback Token",
    )
    callback_url = fields.Char(compute="_compute_callback_url", string="Callback URL")
    callback_uuid = fields.Char(
        copy=False,
        default=lambda self: uuid.uuid4().hex,
        index=True,
        readonly=True,
        required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    connection_mode = fields.Selection(
        CONNECTION_MODES,
        default="callback",
        required=True,
        string="Connection Mode",
        tracking=True,
    )
    encoding_aes_key = fields.Char(
        copy=False,
        groups="qing_wework.group_wework_manager",
        string="EncodingAESKey",
    )
    media_message_count = fields.Integer(compute="_compute_media_message_count")
    media_message_ids = fields.One2many(
        "wework.intelligent.bot.media.message",
        "bot_id",
        string="File Links",
    )
    message_count = fields.Integer(compute="_compute_message_count")
    message_ids = fields.One2many("wework.intelligent.bot.message", "bot_id", string="Messages")
    name = fields.Char(required=True, tracking=True)
    reply_message = fields.Text(
        default="Message received. Processing will begin shortly.",
        string="Immediate Reply",
    )
    websocket_bot_id = fields.Char(
        copy=False,
        groups="qing_wework.group_wework_manager",
        string="WebSocket Bot ID",
    )
    websocket_enabled = fields.Boolean(
        copy=False,
        groups="qing_wework.group_wework_manager",
        string="WebSocket Enabled",
    )
    websocket_last_connected_at = fields.Datetime(readonly=True)
    websocket_last_event = fields.Char(readonly=True)
    websocket_last_error = fields.Text(
        groups="qing_wework.group_wework_manager",
        readonly=True,
    )
    websocket_last_frame_at = fields.Datetime(readonly=True)
    websocket_secret = fields.Char(
        copy=False,
        groups="qing_wework.group_wework_manager",
        string="WebSocket Secret",
    )
    websocket_status = fields.Selection(
        WEBSOCKET_STATES,
        default="stopped",
        readonly=True,
        string="WebSocket Status",
    )
    websocket_url = fields.Char(
        copy=False,
        default=WeChatAIBotWebSocketClient.DEFAULT_WS_URL,
        groups="qing_wework.group_wework_manager",
        required=True,
        string="WebSocket URL",
    )

    @api.constrains("connection_mode", "websocket_bot_id", "websocket_enabled", "websocket_secret")
    def _check_websocket_configuration(self):
        for bot in self:
            if not bot.websocket_enabled or bot.connection_mode != "websocket":
                continue
            if not bot.websocket_bot_id or not bot.websocket_secret:
                raise ValidationError(_("WebSocket Bot ID and WebSocket Secret are required."))

    def action_generate_callback_credentials(self):
        for bot in self:
            bot.write({
                "callback_token": secrets.token_urlsafe(24),
                "encoding_aes_key": bot._generate_encoding_aes_key(),
            })
        return notification_action(
            _("Callback credentials were generated. Update them in the WeCom console."),
            type="success",
            reload=True,
        )

    def action_test_callback_configuration(self):
        self.ensure_one()
        self._get_crypto()
        return notification_action(
            _("The callback credentials are valid. Complete the remote verification in the WeCom console."),
            type="success",
        )

    def action_start_websocket_connection(self):
        self.ensure_one()
        if self.connection_mode != "websocket":
            raise UserError(_("Select WebSocket Long Connection before starting it."))
        if not self.websocket_bot_id or not self.websocket_secret:
            raise UserError(_("Enter the WebSocket Bot ID and WebSocket Secret first."))
        self.write({
            "websocket_enabled": True,
            "websocket_last_error": False,
            "websocket_status": "connecting",
        })
        WeworkIntelligentBotWebSocketManager.ensure_running(self.env.cr.dbname, self.id)
        return notification_action(
            _("The WebSocket connection is starting in the background."),
            type="success",
            reload=True,
        )

    def action_stop_websocket_connection(self):
        self.ensure_one()
        self.write({
            "websocket_enabled": False,
            "websocket_status": "stopped",
        })
        WeworkIntelligentBotWebSocketManager.stop(self.env.cr.dbname, self.id)
        return notification_action(_("The WebSocket connection is stopping."), type="success", reload=True)

    def action_view_media_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Intelligent Bot File Links"),
            "res_model": "wework.intelligent.bot.media.message",
            "view_mode": "list,form",
            "domain": [("bot_id", "=", self.id)],
            "context": {"default_bot_id": self.id},
        }

    def action_view_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Intelligent Bot Messages"),
            "res_model": "wework.intelligent.bot.message",
            "view_mode": "list,form",
            "domain": [("bot_id", "=", self.id)],
            "context": {"default_bot_id": self.id},
        }

    @api.model
    def cron_ensure_websocket_connections(self):
        domain = [
            ("active", "=", True),
            ("connection_mode", "=", "websocket"),
            ("websocket_enabled", "=", True),
        ]
        bots = self.sudo().search(domain)
        for bot in bots:
            WeworkIntelligentBotWebSocketManager.ensure_running(self.env.cr.dbname, bot.id)
        return len(bots)
