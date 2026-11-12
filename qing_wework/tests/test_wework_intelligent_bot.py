# -*- coding: utf-8 -*-

import base64

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWeworkIntelligentBot(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bot = cls.env["wework.intelligent.bot"].create({
            "callback_token": "123456",
            "company_id": cls.env.company.id,
            "encoding_aes_key": "kWxPEV2UEDyxWpmPdKC3F4dgPDmOvfKX1HGnEUDS1aR",
            "name": "Test Intelligent Bot",
            "reply_message": "Message received.",
        })
        cls.payload = {
            "chatid": "chat-1",
            "chattype": "group",
            "from": {"userid": "zhangsan"},
            "msgid": "message-1",
            "msgtype": "text",
            "text": {"content": "Hello Odoo"},
        }

    def test_inbound_message_is_recorded_once(self):
        first_message = self.bot._record_incoming_message(self.payload)
        second_message = self.bot._record_incoming_message(self.payload)

        self.assertEqual(first_message, second_message)
        self.assertEqual(first_message.content, "Hello Odoo")
        self.assertEqual(first_message.conversation_id, "chat-1")
        self.assertEqual(first_message.direction, "inbound")
        self.assertEqual(first_message.sender_user_id, "zhangsan")
        self.assertEqual(first_message.state, "received")

    def test_immediate_reply_is_encrypted_and_logged(self):
        incoming_message = self.bot._record_incoming_message(self.payload)
        response = self.bot._reply_to_callback(
            incoming_message,
            nonce="461056294",
            timestamp="1411525903",
        )
        payload = self.bot._get_crypto().decrypt_message(
            response,
            response["msgsignature"],
            response["timestamp"],
            response["nonce"],
        )
        outgoing_message = self.env["wework.intelligent.bot.message"].search([
            ("bot_id", "=", self.bot.id),
            ("direction", "=", "outbound"),
        ], limit=1)

        self.assertEqual(payload["msgtype"], "stream")
        self.assertEqual(payload["stream"]["content"], "")
        self.assertFalse(payload["stream"]["finish"])
        self.assertTrue(outgoing_message)
        self.assertFalse(outgoing_message.content)
        self.assertEqual(outgoing_message.state, "sent")
        self.assertTrue(outgoing_message.stream_id)

    def test_stream_callback_closes_reply(self):
        incoming_message = self.bot._record_incoming_message({
            "msgid": "stream-message-1",
            "msgtype": "stream",
            "stream": {"id": "stream-1"},
        })
        response = self.bot._reply_to_callback(
            incoming_message,
            nonce="461056294",
            timestamp="1411525903",
        )
        payload = self.bot._get_crypto().decrypt_message(
            response,
            response["msgsignature"],
            response["timestamp"],
            response["nonce"],
        )

        self.assertEqual(payload["msgtype"], "stream")
        self.assertEqual(payload["stream"]["id"], "stream-1")
        self.assertEqual(payload["stream"]["content"], "Message received.")
        self.assertTrue(payload["stream"]["finish"])

    def test_websocket_reply_uses_a_stream_and_records_the_message(self):
        self.bot.write({
            "connection_mode": "websocket",
            "websocket_bot_id": "bot-id",
            "websocket_enabled": True,
            "websocket_secret": "bot-secret",
        })
        incoming_message = self.bot._record_incoming_message({
            "from": {"userid": "zhangsan"},
            "msgid": "websocket-message-1",
            "msgtype": "text",
            "text": {"content": "Hello Odoo"},
        })

        payload = self.bot._prepare_websocket_reply(incoming_message)
        outgoing_message = self.env["wework.intelligent.bot.message"].search([
            ("bot_id", "=", self.bot.id),
            ("direction", "=", "outbound"),
            ("stream_id", "=", payload["stream"]["id"]),
        ])

        self.assertEqual(payload["msgtype"], "stream")
        self.assertTrue(payload["stream"]["finish"])
        self.assertEqual(payload["stream"]["content"], "Message received.")
        self.assertTrue(outgoing_message)

    def test_generate_callback_credentials(self):
        self.bot.action_generate_callback_credentials()

        self.assertEqual(len(self.bot.callback_token), 32)
        self.assertEqual(len(self.bot.encoding_aes_key), 43)
        self.assertEqual(
            len(base64.b64decode(self.bot.encoding_aes_key + "=", validate=True)),
            32,
        )
        self.bot._get_crypto()
