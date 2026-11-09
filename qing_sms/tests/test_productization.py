from datetime import timedelta
import json
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_sms import SmsFixture


@tagged('post_install', '-at_install')
class TestSmsProductization(SmsFixture):
    def test_blueprint_schema_and_public_catalog_protection(self):
        Blueprint = self.env['qing.sms.blueprint']
        values = {'name': 'Test blueprint', 'code': 'test.notification',
                  'company_id': self.env.company.id, 'content': 'Hello ${order}',
                  'variable_schema': [{'key': 'order', 'type': 'text', 'max_length': 30}]}
        blueprint = Blueprint.create(values)
        self.assertEqual(blueprint.variable_schema[0]['key'], 'order')
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Blueprint.create(dict(values, code='invalid.notification', variable_schema=[]))
        public = Blueprint.sudo().create(dict(values, code='public.notification', company_id=False))
        with self.assertRaises(AccessError):
            public.with_user(self.sender).write({'name': 'Changed'})

    def test_template_application_freezes_and_never_auto_recreates_unknown(self):
        Application = self.env['qing.sms.template.application']
        application = Application.create({'name': 'Test application', 'account_id': self.account.id,
            'sign_id': self.sign.id, 'content': '订单 ${order_no} 已发货',
            'variable_schema': [{'key': 'order_no', 'type': 'text', 'max_length': 30}],
            'provider_variable_rules': {'order_no': 'characterWithNumber'}})
        application.action_queue()
        self.assertEqual(application.state, 'queued')
        payload_hash = application.payload_hash
        application.action_queue()
        self.assertEqual(application.payload_hash, payload_hash)
        with self.assertRaises(AccessError):
            application.write({'content': 'changed'})
        application._write_internal({'state': 'unknown'})
        self.assertNotIn(application.id, Application.search([('state', '=', 'queued')]).ids)
        application.action_link_remote(self.remote)
        self.assertEqual(application.state, 'approved')

    def test_native_template_creates_one_tracked_domestic_intent(self):
        partner = self.env['res.partner'].create({'name': 'Customer', 'phone': '13800138000'})
        self.binding.variable_ids.write({'source': 'field', 'field_path': 'name'})
        self.binding.action_review()
        message = partner.with_user(self.sender)._message_sms_with_template(template=self.template,
            partner_ids=partner.ids, put_in_queue=False)
        self.assertEqual(message._name, 'mail.message')
        domestic = self.env['qing.sms.message'].search([('res_model', '=', 'res.partner'),
                                                        ('res_id', '=', partner.id)])
        native_sms = self.env['sms.sms'].search([('mail_message_id', '=', message.id)])
        self.assertEqual(len(domestic), 1, f'partner_ids={message.partner_ids.ids}; create_uid={message.create_uid.id}; env_uid={self.env.user.id}; sms={[(item.number, item.state, item.body, item.failure_type) for item in native_sms]}')
        self.assertEqual(domestic.sms_id.mail_message_id, message)
        self.assertEqual(domestic.event_id.event_code, 'native.template')
        self.assertEqual(domestic.event_id.state, 'enqueued')
        notification = self.env['mail.notification'].search([('sms_id_int', '=', domestic.sms_id.id)])
        self.assertEqual(len(notification), 1)
        self.assertEqual(notification.sms_tracker_ids.sms_uuid, domestic.sms_uuid)
        other_user = self.env['res.users'].create({'name': 'Other SMS sender', 'login': 'other-sms-sender',
            'group_ids': [Command.set([self.env.ref('qing_sms.group_sms_user').id])],
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)]})
        self.assertEqual(domestic.with_user(self.sender).read(['number'])[0]['number'], '+8613800138000')
        with self.assertRaises(AccessError):
            domestic.with_user(other_user).read(['number'])
        with self.assertRaises(AccessError):
            domestic.event_id.with_user(other_user).read(['event_key'])
        self.adapter.send.assert_not_called()

    def test_scheduled_native_template_keeps_original_binding_version(self):
        partner = self.env['res.partner'].create({'name': 'Scheduled recipient', 'phone': '13800138000'})
        self.binding.variable_ids.write({'source': 'field', 'field_path': 'name'})
        self.binding.action_review()
        message = partner.with_user(self.sender)._message_sms_with_template(template=self.template,
            partner_ids=partner.ids, scheduled_date=fields.Datetime.now() + timedelta(hours=1))
        schedule = self.env['mail.message.schedule'].search([('mail_message_id', '=', message.id)])
        self.assertEqual(len(schedule), 1)
        event = self.env['qing.sms.event'].search([('mail_message_id', '=', message.id)])
        self.assertEqual(event.state, 'pending')
        self.assertEqual(event.sender_user_id, self.sender)
        self.assertEqual(len(event.recipient_snapshot), 1)
        parameters = json.loads(schedule.notification_parameters)
        self.assertEqual(parameters['qing_domestic_event_id'], event.id)
        self.assertNotIn('qing_domestic_values', parameters)
        self.assertNotIn('qing_domestic_sender_user_id', parameters)
        self.assertFalse(self.env['qing.sms.message'].search([('res_id', '=', partner.id)]))
        self.binding._write_internal({'accept_new': False, 'retired_at': fields.Datetime.now()})
        schedule._send_notifications()
        domestic = self.env['qing.sms.message'].search([('res_model', '=', 'res.partner'),
                                                        ('res_id', '=', partner.id)])
        self.assertEqual(len(domestic), 1)
        self.assertEqual(domestic.binding_id, self.binding)
        self.assertEqual(domestic.event_id, event)
        domestic._preflight()
        self.adapter.send.assert_not_called()

    def test_scheduled_native_template_rechecks_original_sender(self):
        partner = self.env['res.partner'].create({'name': 'Scheduled recipient', 'phone': '13800138000'})
        self.binding.variable_ids.write({'source': 'field', 'field_path': 'name'})
        self.binding.action_review()
        message = partner.with_user(self.sender)._message_sms_with_template(template=self.template,
            partner_ids=partner.ids, scheduled_date=fields.Datetime.now() + timedelta(hours=1))
        event = self.env['qing.sms.event'].search([('mail_message_id', '=', message.id)])
        self.sender.write({'active': False})
        self.env['mail.message.schedule'].search([('mail_message_id', '=', message.id)])._send_notifications()
        self.assertEqual(event.state, 'blocked')
        self.assertFalse(event.message_ids)
        self.assertFalse(self.env['qing.sms.message'].search([('res_id', '=', partner.id)]))
        self.adapter.send.assert_not_called()

    def test_native_template_mixed_numbers_never_routes_mainland_to_iap(self):
        domestic_partner = self.env['res.partner'].create({'name': 'Domestic', 'phone': '13800138000'})
        foreign_partner = self.env['res.partner'].create({'name': 'Foreign', 'phone': '+14155552671'})
        self.binding.variable_ids.write({'source': 'field', 'field_path': 'name'})
        self.binding.action_review()
        with patch('odoo.addons.sms.models.sms_sms.SmsSms._send') as native_send:
            message = domestic_partner.with_user(self.sender)._message_sms_with_template(
                template=self.template, partner_ids=(domestic_partner + foreign_partner).ids,
                sms_numbers=['13800138000', '+14155552671'])
        domestic = self.env['qing.sms.message'].search([('res_id', '=', domestic_partner.id)])
        native = self.env['sms.sms'].search([('mail_message_id', '=', message.id)]) - domestic.sms_id
        self.assertEqual(len(domestic), 1)
        self.assertEqual(len(native), 1)
        self.assertEqual(native.number, '+14155552671')
        native_send.assert_called_once()
        self.assertEqual(len(self.env['mail.notification'].search([
            ('mail_message_id', '=', message.id), ('notification_type', '=', 'sms')])), 2)
        self.adapter.send.assert_not_called()

    def test_otp_native_template_is_rejected_before_chatter_creation(self):
        partner = self.env['res.partner'].create({'name': 'OTP recipient', 'phone': '13800138000'})
        self.remote._write_synced({'category': 'otp'})
        self.binding.action_review()
        domain = [('model', '=', 'res.partner'), ('res_id', '=', partner.id)]
        before = self.env['mail.message'].search_count(domain)
        with self.assertRaises(UserError):
            partner.with_user(self.sender)._message_sms_with_template(template=self.template,
                partner_ids=partner.ids)
        self.assertEqual(self.env['mail.message'].search_count(domain), before)
        self.adapter.send.assert_not_called()
