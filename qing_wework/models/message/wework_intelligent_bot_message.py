# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError


CONVERSATION_TYPES = [
    ("single", "Single Chat"),
    ("group", "Group Chat"),
]

MESSAGE_DIRECTIONS = [
    ("inbound", "Inbound"),
    ("outbound", "Outbound"),
]

MESSAGE_STATES = [
    ("received", "Received"),
    ("sent", "Sent"),
]


class WeworkIntelligentBotMessage(models.Model):
    _name = "wework.intelligent.bot.message"
    _description = "WeCom Intelligent Bot Message"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    _external_message_unique = models.Constraint(
        "UNIQUE(bot_id, external_message_id)",
        "An intelligent bot message with this external ID already exists.",
    )

    @api.depends("direction", "message_type", "sender_user_id")
    def _compute_name(self):
        for message in self:
            direction = dict(MESSAGE_DIRECTIONS).get(message.direction, "Message")
            message_type = message.message_type or "event"
            sender = " - %s" % message.sender_user_id if message.sender_user_id else ""
            message.name = "%s %s%s" % (direction, message_type, sender)

    bot_id = fields.Many2one(
        "wework.intelligent.bot",
        ondelete="cascade",
        required=True,
        readonly=True,
    )
    content = fields.Text(readonly=True)
    conversation_id = fields.Char(index=True, readonly=True)
    conversation_type = fields.Selection(CONVERSATION_TYPES, readonly=True)
    direction = fields.Selection(MESSAGE_DIRECTIONS, required=True, readonly=True)
    external_message_id = fields.Char(index=True, readonly=True)
    message_type = fields.Char(readonly=True)
    name = fields.Char(compute="_compute_name", readonly=True)
    payload = fields.Text(readonly=True)
    received_at = fields.Datetime(readonly=True)
    response_url = fields.Char(
        groups="qing_wework.group_wework_manager",
        readonly=True,
    )
    sender_user_id = fields.Char(index=True, readonly=True)
    sent_at = fields.Datetime(readonly=True)
    state = fields.Selection(MESSAGE_STATES, required=True, readonly=True)
    stream_id = fields.Char(index=True, readonly=True)
    websocket_request_id = fields.Char(
        groups="qing_wework.group_wework_manager",
        index=True,
        readonly=True,
    )

    def action_create_media_message(self):
        self.ensure_one()
        if self.direction != "inbound" or not self.conversation_id:
            return False
        if self.bot_id.connection_mode == "callback" and not self.response_url:
            raise UserError(_("This inbound message does not include a temporary response URL."))
        return {
            "type": "ir.actions.act_window",
            "name": "Send Intelligent Bot Download Link",
            "res_model": "wework.intelligent.bot.media.message",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_bot_id": self.bot_id.id,
                "default_conversation_id": self.conversation_id,
                "default_conversation_type": self.conversation_type,
                "default_incoming_message_id": self.id,
            },
        }
