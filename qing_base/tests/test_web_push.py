import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestQingWebPush(TransactionCase):
    def _device(self):
        return self.env['mail.push.device'].sudo().create({
            'partner_id': self.env.user.partner_id.id,
            'endpoint': 'https://push.example.invalid/qing-test',
            'keys': json.dumps({'p256dh': 'test', 'auth': 'test'}),
        })

    def test_cached_client_cannot_register_device(self):
        devices = self.env['mail.push.device'].sudo()
        before = devices.search_count([])
        devices.register_devices(
            endpoint='https://push.example.invalid/new',
            keys={'p256dh': 'test', 'auth': 'test'},
            vapid_public_key='obsolete-key',
        )
        self.assertEqual(devices.search_count([]), before)

    def test_existing_devices_cannot_receive_direct_or_queued_push(self):
        device = self._device()
        thread = self.env['mail.thread']
        devices, private_key, public_key = thread._web_push_get_partners_parameters(
            [self.env.user.partner_id.id],
        )
        self.assertFalse(devices)
        self.assertIsNone(private_key)
        self.assertIsNone(public_key)
        queue = self.env['mail.push'].sudo()
        before = queue.search_count([])
        with patch('odoo.addons.mail.models.mail_thread.Session') as session:
            for direct in (True, False):
                thread._web_push_send_notification(
                    device, 'private', 'public', payload={'body': 'test'},
                    force_direct_send=direct,
                )
            session.assert_not_called()
        self.assertEqual(queue.search_count([]), before)
        self.assertTrue(device.exists())

    def test_legacy_queue_never_contacts_endpoint(self):
        queued = self.env['mail.push'].sudo().create({
            'mail_push_device_id': self._device().id,
            'payload': json.dumps({'body': 'legacy'}),
        })
        with patch('odoo.addons.mail.models.mail_push.Session') as session:
            self.env['mail.push']._push_notification_to_endpoint()
            session.assert_not_called()
        self.assertTrue(queued.exists())

    def test_regular_chatter_message_is_preserved(self):
        partner = self.env['res.partner'].create({'name': 'QingERP message test'})
        recipient = new_test_user(
            self.env, login='qing_push_recipient', notification_type='inbox',
        )
        author = self.env['res.partner'].create({'name': 'QingERP message author'})
        message = partner.with_context(mail_notify_force_send=False).message_post(
            body='QingERP in-app chatter',
            author_id=author.id,
            partner_ids=[recipient.partner_id.id],
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )
        self.assertTrue(message.exists())
        self.assertIn(message, partner.message_ids)
        self.assertIn('QingERP in-app chatter', message.body)
        notifications = self.env['mail.notification'].search([
            ('mail_message_id', '=', message.id),
            ('res_partner_id', '=', recipient.partner_id.id),
        ])
        self.assertEqual(notifications.notification_type, 'inbox')
