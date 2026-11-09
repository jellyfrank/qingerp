from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user


class TestDiscuss(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = new_test_user(cls.env, login='chat_operator', groups='qing_whatsapp.group_user')
        cls.outsider = new_test_user(cls.env, login='chat_outsider', groups='base.group_user')
        cls.account = cls.env['qing.whatsapp.account'].create({'name': 'Chat account', 'phone_uid': '8765432',
            'waba_uid': '8765432', 'operator_ids': [(6, 0, cls.operator.ids)]})
        cls.conversation = cls.env['qing.whatsapp.conversation']._find_or_create(cls.account, '+32499000000')
        cls.conversation.last_customer_at = fields.Datetime.now()

    def test_discuss_reply_and_private_notes(self):
        channel = self.conversation._ensure_discuss_channel()
        posted = channel.with_user(self.operator).message_post(body='<p>Hello customer</p>', message_type='comment')
        message = self.env['qing.whatsapp.message'].search([('discuss_message_id', '=', posted.id)])
        self.assertEqual(message.payload['text']['body'], 'Hello customer')
        self.assertEqual(message.sender_id, self.operator)
        self.assertEqual(message.state, 'queued')
        count = self.env['qing.whatsapp.message'].search_count([])
        self.conversation.with_user(self.operator).message_post(body='Internal only', subtype_xmlid='mail.mt_note')
        self.assertEqual(self.env['qing.whatsapp.message'].search_count([]), count)

    def test_revocation_and_invitation(self):
        channel = self.conversation._ensure_discuss_channel()
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['discuss.channel.member'].sudo().create({'channel_id': channel.id, 'partner_id': self.outsider.partner_id.id})
        self.assertFalse(channel.with_user(self.outsider).has_access('read'))
        self.account.operator_ids = False
        self.assertFalse(channel.channel_member_ids)
        with self.assertRaises(AccessError):
            channel.with_user(self.operator).message_post(body='Cannot send', message_type='comment')

    def test_inbound_and_closed_window(self):
        value = {'id': 'wamid.chat_test', 'from': '32499000000', 'timestamp': str(int(fields.Datetime.now().timestamp())),
                 'type': 'text', 'text': {'body': 'Customer reply'}}
        self.env['qing.whatsapp.event']._receive(self.account, 'message', value)._process()
        message = self.env['qing.whatsapp.message'].search([('provider_uid', '=', 'wamid.chat_test')])
        self.assertEqual(message.discuss_message_id.model, 'discuss.channel')
        self.assertIn('Customer reply', message.discuss_message_id.body)
        self.conversation.last_customer_at = False
        with self.assertRaises(UserError), self.cr.savepoint():
            self.conversation.discuss_channel_id.with_user(self.operator).message_post(body='Closed', message_type='comment')
