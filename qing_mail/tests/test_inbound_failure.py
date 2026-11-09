import hashlib
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase
from odoo.tools.binary import BinaryBytes


PAYLOAD = (
    b'From: sender@example.invalid\r\n'
    b'To: sales@example.invalid\r\n'
    b'Message-ID: <qing-mail-test@example.invalid>\r\n'
    b'Subject: Test inbound recovery\r\n\r\n'
    b'Test body'
)


class TestInboundFailure(TransactionCase):
    def setUp(self):
        super().setUp()
        server = self.env['fetchmail.server'].create({
            'name': 'Recovery Test',
            'server_type': 'imap',
            'server': 'imap.example.invalid',
            'port': 993,
            'is_ssl': True,
            'user': 'sales@example.invalid',
            'password': 'test-only-code',
        })
        self.failure = self.env['qing.mail.inbound.failure'].sudo().create({
            'server_id': server.id,
            'payload': BinaryBytes(PAYLOAD),
            'payload_sha256': hashlib.sha256(PAYLOAD).hexdigest(),
            'payload_size': len(PAYLOAD),
            'failure_type': 'ValueError',
        })

    def test_retry_keeps_a_failure_and_records_attempt(self):
        with patch('odoo.addons.mail.models.mail_thread.MailThread.message_process',
                   side_effect=ValueError('simulated parser error')):
            self.failure.action_retry()
        self.assertEqual(self.failure.state, 'pending')
        self.assertEqual(self.failure.retry_count, 1)
        self.assertEqual(self.failure._decode_payload(), PAYLOAD)

    def test_retry_resolves_and_drops_sensitive_payload(self):
        with patch('odoo.addons.mail.models.mail_thread.MailThread.message_process',
                   return_value=True):
            self.failure.action_retry()
        self.assertEqual(self.failure.state, 'resolved')
        self.assertFalse(self.failure.payload)
        self.assertEqual(self.failure.retry_count, 1)

    def test_pending_copy_cannot_be_deleted(self):
        with self.assertRaises(UserError):
            self.failure.unlink()

    def test_pending_copy_cannot_be_marked_resolved_without_replay(self):
        with self.assertRaises(UserError):
            self.failure.state = 'resolved'

    def test_tampered_payload_cannot_be_replayed(self):
        self.failure.payload = BinaryBytes(b'altered')
        with self.assertRaises(UserError):
            self.failure.action_retry()
