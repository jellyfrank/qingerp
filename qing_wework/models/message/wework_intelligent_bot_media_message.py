# -*- coding: utf-8 -*-

import base64
import json
import logging
from datetime import timedelta
from urllib.parse import urlencode

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import BinaryValue
from ...tools.actions import notification_action


_logger = logging.getLogger(__name__)

MEDIA_MESSAGE_STATES = [
    ("draft", "Draft"),
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("retry", "Retry"),
    ("sent", "Sent"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]

PENDING_MEDIA_MESSAGE_STATES = ["pending", "retry"]
STALE_MEDIA_MESSAGE_MINUTES = 30


class WeworkIntelligentBotMediaMessage(models.Model):
    _name = "wework.intelligent.bot.media.message"
    _description = "WeCom Intelligent Bot File Link"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    @api.depends("source_file_name", "state")
    def _compute_name(self):
        for message in self:
            filename = message.source_file_name or _("Untitled file")
            message.name = "%s (%s: %s)" % (
                filename,
                dict(MEDIA_MESSAGE_STATES).get(message.state, message.state),
                _("Download link"),
            )

    def _get_download_url(self):
        self.ensure_one()
        if self.download_url:
            return self.download_url

        attachment_domain = [
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("res_field", "=", "source_file"),
        ]
        attachment = self.env["ir.attachment"].sudo().search(attachment_domain, limit=1)
        if not attachment:
            raise UserError(_("The uploaded file could not be found."))

        # Native per-attachment token: only this file, no extra share model.
        token = attachment.generate_access_token()[0]
        self.download_url = "%s/web/content/%s?%s" % (
            self.get_base_url(),
            attachment.id,
            urlencode({"access_token": token, "download": "true"}),
        )
        return self.download_url

    def _get_markdown_content(self):
        self.ensure_one()
        filename = (self.source_file_name or _("Download file")).replace("[", "\\[").replace("]", "\\]")
        return _("File ready: [%(filename)s](%(url)s)") % {
            "filename": filename,
            "url": self._get_download_url(),
        }

    def _claim_pending_messages(self, batch_size):
        self.flush_model(["next_run", "state"])
        self.env.cr.execute(
            """
                WITH claimed AS (
                    SELECT id
                      FROM wework_intelligent_bot_media_message
                     WHERE state = ANY(%s)
                       AND next_run <= NOW()
                       AND bot_id IN (
                           SELECT id
                             FROM wework_intelligent_bot
                            WHERE connection_mode = 'callback'
                       )
                     ORDER BY id
                     FOR UPDATE SKIP LOCKED
                     LIMIT %s
                )
                UPDATE wework_intelligent_bot_media_message AS message
                   SET started_at = NOW(),
                       state = 'processing',
                       write_date = NOW(),
                       write_uid = %s
                  FROM claimed
                 WHERE message.id = claimed.id
             RETURNING message.id
            """,
            [PENDING_MEDIA_MESSAGE_STATES, max(batch_size, 1), self.env.uid],
        )
        message_ids = [row[0] for row in self.env.cr.fetchall()]
        self.invalidate_model()
        return self.sudo().browse(message_ids)

    def _claim_pending_websocket_messages(self, bot_id, batch_size=10):
        self.flush_model(["next_run", "state"])
        self.env.cr.execute(
            """
                WITH claimed AS (
                    SELECT id
                      FROM wework_intelligent_bot_media_message
                     WHERE bot_id = %s
                       AND state = ANY(%s)
                       AND next_run <= NOW()
                     ORDER BY id
                     FOR UPDATE SKIP LOCKED
                     LIMIT %s
                )
                UPDATE wework_intelligent_bot_media_message AS message
                   SET started_at = NOW(),
                       state = 'processing',
                       write_date = NOW(),
                       write_uid = %s
                  FROM claimed
                 WHERE message.id = claimed.id
             RETURNING message.id
            """,
            [bot_id, PENDING_MEDIA_MESSAGE_STATES, max(batch_size, 1), self.env.uid],
        )
        message_ids = [row[0] for row in self.env.cr.fetchall()]
        self.invalidate_model()
        return self.sudo().browse(message_ids)

    def _mark_retry(self, error):
        self.ensure_one()
        attempts = self.attempts + 1
        state = "failed" if attempts >= 3 else "retry"
        values = {
            "attempts": attempts,
            "error_message": str(error),
            "finished_at": fields.Datetime.now() if state == "failed" else False,
            "next_run": fields.Datetime.now() + timedelta(minutes=min(60, 2 ** attempts)),
            "state": state,
        }
        self.write(values)

    def _reset_stale_messages(self):
        stale_before = fields.Datetime.now() - timedelta(minutes=STALE_MEDIA_MESSAGE_MINUTES)
        stale_domain = [("state", "=", "processing"), ("started_at", "<", stale_before)]
        stale_messages = self.sudo().search(stale_domain)
        stale_messages.write({
            "error_message": _(
                "The worker did not finish within %s minutes; the file link was re-queued."
            ) % STALE_MEDIA_MESSAGE_MINUTES,
            "next_run": fields.Datetime.now(),
            "state": "retry",
        })

    def _send(self):
        self.ensure_one()
        if not self.source_file:
            raise UserError(_("Please upload a file before sending it."))
        if not self.incoming_message_id.response_url:
            raise UserError(_("The source message has no temporary response URL for sending the download link."))

        payload = {
            "msgtype": "markdown",
            "markdown": {"content": self._get_markdown_content()},
        }
        response = requests.post(
            self.incoming_message_id.response_url,
            json=payload,
            timeout=(5, 20),
        )
        response.raise_for_status()
        try:
            response_data = response.json()
        except ValueError:
            response_data = {}
        if response_data.get("errcode") not in (None, 0):
            raise UserError(response_data.get("errmsg") or _("WeCom rejected the download link."))
        self.write({
            "error_message": False,
            "finished_at": fields.Datetime.now(),
            "response_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "sent_at": fields.Datetime.now(),
            "state": "sent",
        })

    def _send_websocket(self, client):
        self.ensure_one()
        if self.bot_id.connection_mode != "websocket":
            raise UserError(_("This delivery is not configured for a WebSocket connection."))
        if not self.source_file:
            raise UserError(_("Please upload a file before sending it."))
        source_file = self.source_file
        if isinstance(source_file, BinaryValue):
            source_file = source_file.content
        elif isinstance(source_file, str):
            source_file = base64.b64decode(source_file)
        media_id = client.upload_media(
            "file",
            self.source_file_name or "file",
            source_file,
        )
        client.send_media_message(self.conversation_id, "file", media_id)
        payload = {
            "file": {"media_id": media_id},
            "msgtype": "file",
        }
        self.write({
            "error_message": False,
            "finished_at": fields.Datetime.now(),
            "response_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "sent_at": fields.Datetime.now(),
            "state": "sent",
        })

    def _trigger_cron(self):
        cron = self.env.ref(
            "qing_wework.ir_cron_wework_intelligent_bot_media_message",
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo()._trigger()

    attempts = fields.Integer(default=0, readonly=True)
    bot_id = fields.Many2one(
        "wework.intelligent.bot",
        ondelete="cascade",
        required=True,
    )
    conversation_id = fields.Char(index=True, required=True)
    conversation_type = fields.Selection(
        [("single", "Single Chat"), ("group", "Group Chat")],
        required=True,
    )
    download_url = fields.Char(
        groups="qing_wework.group_wework_manager",
        readonly=True,
    )
    error_message = fields.Text(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    incoming_message_id = fields.Many2one(
        "wework.intelligent.bot.message",
        ondelete="set null",
        readonly=True,
    )
    name = fields.Char(compute="_compute_name", readonly=True)
    next_run = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    requested_by_id = fields.Many2one("res.users", readonly=True)
    response_payload = fields.Text(
        groups="qing_wework.group_wework_manager",
        readonly=True,
    )
    sent_at = fields.Datetime(readonly=True)
    source_file = fields.Binary(attachment=True, required=True)
    source_file_name = fields.Char(required=True)
    started_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        MEDIA_MESSAGE_STATES,
        default="draft",
        index=True,
        readonly=True,
        required=True,
        tracking=True,
    )

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            values.setdefault("requested_by_id", self.env.user.id)
        return super().create(values_list)

    def action_cancel(self):
        self.filtered(lambda message: message.state in PENDING_MEDIA_MESSAGE_STATES + ["processing"]).write({
            "finished_at": fields.Datetime.now(),
            "state": "cancelled",
        })
        return True

    def action_queue_send(self):
        for message in self:
            if not message.source_file:
                raise UserError(_("Please upload a file before sending it."))
            if (
                message.bot_id.connection_mode == "callback"
                and not message.incoming_message_id.response_url
            ):
                raise UserError(_("Open Send File from an inbound intelligent bot message."))
            message.write({
                "attempts": 0,
                "error_message": False,
                "finished_at": False,
                "next_run": fields.Datetime.now(),
                "state": "pending",
            })
        self._trigger_cron()
        return notification_action(_("The intelligent bot file link has been queued."), type="success")

    def action_retry(self):
        self.write({
            "attempts": 0,
            "error_message": False,
            "finished_at": False,
            "next_run": fields.Datetime.now(),
            "state": "pending",
        })
        self._trigger_cron()
        return True

    @api.model
    def cron_process_messages(self, batch_size=10):
        self._reset_stale_messages()
        messages = self._claim_pending_messages(batch_size)
        for message in messages:
            try:
                message._send()
            except Exception as error:
                _logger.exception("[QingERP WeCom] Intelligent bot file link %s failed", message.id)
                message._mark_retry(error)
        try:
            self.env["ir.cron"]._commit_progress(len(messages), remaining=0)
        except Exception:
            _logger.debug("WeCom intelligent bot media cron progress notification skipped.", exc_info=True)
        return bool(messages)
