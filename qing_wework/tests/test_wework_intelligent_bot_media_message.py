# -*- coding: utf-8 -*-

from unittest.mock import Mock, patch

from odoo.tests.common import TransactionCase, tagged
from odoo.tools import BinaryBytes


@tagged("post_install", "-at_install")
class TestWeworkIntelligentBotMediaMessage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bot = cls.env["wework.intelligent.bot"].create({
            "callback_token": "123456",
            "company_id": cls.env.company.id,
            "encoding_aes_key": "kWxPEV2UEDyxWpmPdKC3F4dgPDmOvfKX1HGnEUDS1aR",
            "name": "Test Intelligent Bot",
        })

        cls.inbound_message = cls.bot._record_incoming_message({
            "from": {"userid": "zhangsan"},
            "msgid": "file-link-context-message",
            "msgtype": "text",
            "response_url": "https://wecom.example.test/response-url",
            "text": {"content": "Please send the PDF."},
        })

    def _create_media_message(self):
        return self.env["wework.intelligent.bot.media.message"].create({
            "bot_id": self.bot.id,
            "conversation_id": "zhangsan",
            "conversation_type": "single",
            "incoming_message_id": self.inbound_message.id,
            "source_file": BinaryBytes(b"pdf-content"),
            "source_file_name": "test.pdf",
        })

    def test_media_message_sends_markdown_download_link_to_response_url(self):
        media_message = self._create_media_message()
        response = Mock()
        response.json.return_value = {"errcode": 0}

        with patch(
            "odoo.addons.qing_wework.models.message.wework_intelligent_bot_media_message.requests.post",
            return_value=response,
        ) as post:
            media_message.write({"state": "processing"})
            media_message._send()

        self.assertEqual(media_message.state, "sent")
        self.assertIn("/web/content/", media_message.download_url)
        self.assertIn("access_token=", media_message.download_url)
        self.assertEqual(post.call_args.args[0], self.inbound_message.response_url)
        self.assertEqual(post.call_args.kwargs["json"]["msgtype"], "markdown")
        self.assertIn("test.pdf", post.call_args.kwargs["json"]["markdown"]["content"])
        response.raise_for_status.assert_called_once()

    def test_cron_claims_and_sends_pending_file_link(self):
        media_message = self._create_media_message()
        response = Mock()
        response.json.return_value = {"errcode": 0}
        media_message.write({"state": "processing"})

        with patch.object(type(self.env["ir.cron"]), "_commit_progress", return_value=True):
            with patch.object(
                type(media_message),
                "_claim_pending_messages",
                return_value=media_message,
            ):
                with patch(
                    "odoo.addons.qing_wework.models.message.wework_intelligent_bot_media_message.requests.post",
                    return_value=response,
                ) as post:
                    self.env["wework.intelligent.bot.media.message"].cron_process_messages(batch_size=1)

        self.assertEqual(media_message.state, "sent")
        post.assert_called_once()

    def test_inbound_message_creates_media_message_for_same_conversation(self):
        inbound_message = self.bot._record_incoming_message({
            "from": {"userid": "zhangsan"},
            "msgid": "media-context-message",
            "msgtype": "text",
            "response_url": "https://wecom.example.test/response-url",
            "text": {"content": "Please send the PDF."},
        })

        action = inbound_message.action_create_media_message()

        self.assertEqual(action["res_model"], "wework.intelligent.bot.media.message")
        self.assertEqual(action["context"]["default_bot_id"], self.bot.id)
        self.assertEqual(action["context"]["default_conversation_id"], "zhangsan")
        self.assertEqual(action["context"]["default_incoming_message_id"], inbound_message.id)

    def test_media_message_uploads_and_sends_file_over_websocket(self):
        class WebSocketClient(object):
            def __init__(self):
                self.uploads = []
                self.sent_messages = []

            def upload_media(self, media_type, filename, content):
                self.uploads.append((media_type, filename, content))
                return "media-id"

            def send_media_message(self, conversation_id, media_type, media_id):
                self.sent_messages.append((conversation_id, media_type, media_id))

        self.bot.write({
            "connection_mode": "websocket",
            "websocket_bot_id": "bot-id",
            "websocket_enabled": True,
            "websocket_secret": "bot-secret",
        })
        media_message = self._create_media_message()
        client = WebSocketClient()

        media_message.write({"state": "processing"})
        media_message._send_websocket(client)

        self.assertEqual(media_message.state, "sent")
        self.assertEqual(client.uploads, [("file", "test.pdf", b"pdf-content")])
        self.assertEqual(client.sent_messages, [("zhangsan", "file", "media-id")])
