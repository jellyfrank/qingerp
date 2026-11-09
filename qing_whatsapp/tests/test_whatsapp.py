import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user
from ..services.cloud_api import CloudAPI, CloudError, valid_signature
from ..services.commerce import product_message, product_list, flow_message, shop_link


class TestWhatsapp(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = new_test_user(cls.env, login='wa_operator', groups='qing_whatsapp.group_user')
        cls.manager = new_test_user(cls.env, login='wa_manager', groups='qing_whatsapp.group_manager')
        cls.visitor = new_test_user(cls.env, login='wa_visitor', groups='base.group_user')
        cls.account = cls.env['qing.whatsapp.account'].create({'name': 'Test WA', 'phone_uid': '12345',
            'waba_uid': '67890', 'access_token': 'test-token', 'app_secret': 'test-secret',
            'operator_ids': [(6, 0, cls.operator.ids)]})
        cls.conversation = cls.env['qing.whatsapp.conversation']._find_or_create(cls.account, '+32499000000')
        cls.conversation.last_customer_at = fields.Datetime.now()
        cls.template = cls.env['qing.whatsapp.template'].create({'name': 'test_order', 'account_id': cls.account.id,
            'language': 'en_US', 'status': 'APPROVED', 'remote_uid': '100'})
        cls.Message = cls.env['qing.whatsapp.message']
        cls.Event = cls.env['qing.whatsapp.event']

    def enqueue(self, key='request1', **kwargs):
        return self.Message.with_user(self.operator)._enqueue(self.conversation, 'text', {'body': 'hello'}, request_key=key, **kwargs)

    def event(self, uid='in1', kind='text', content=None):
        value = {'id': uid, 'from': '32499000000', 'timestamp': str(int(fields.Datetime.now().timestamp())), 'type': kind,
                 kind: content or {'body': 'hello'}}
        return self.Event._receive(self.account, 'message', value)

    def test_enqueue_is_async_and_idempotent(self):
        with patch.object(CloudAPI, 'send') as send:
            message = self.enqueue()
            self.assertEqual(message.state, 'queued')
            self.assertEqual(self.enqueue(), message)
            send.assert_not_called()
        with self.assertRaises(ValidationError):
            self.Message.with_user(self.operator)._enqueue(self.conversation, 'text', {'body': 'different'}, request_key='request1')

    def test_no_window_no_free_text(self):
        self.conversation.last_customer_at = fields.Datetime.now() - timedelta(hours=25)
        with self.assertRaises(UserError):
            self.enqueue()
        message = self.Message.with_user(self.operator)._enqueue(self.conversation, 'template', [],
            request_key='template', template=self.template)
        self.assertEqual(message.state, 'queued')

    def test_preflight_rechecks_user(self):
        message = self.enqueue()
        self.operator.active = False
        with self.assertRaises(AccessError):
            message._preflight()

    def test_preflight_rechecks_window_and_template(self):
        message = self.enqueue()
        self.conversation.last_customer_at = fields.Datetime.now() - timedelta(hours=25)
        with self.assertRaises(UserError):
            message._preflight()
        template_msg = self.Message.with_user(self.operator)._enqueue(self.conversation, 'template', [], request_key='tpl', template=self.template)
        self.template.status = 'PAUSED'
        with self.assertRaises(UserError):
            template_msg._preflight()

    def test_status_never_regresses(self):
        message = self.enqueue()
        message._apply_status('read', 'wamid.1')
        message._apply_status('delivered')
        message._apply_status('sent')
        message._apply_status('failed')
        message._record_transport_error({'state': 'unknown'})
        self.assertEqual(message.state, 'read')

    def test_early_receipt_waits_then_matches(self):
        event = self.Event._receive(self.account, 'status', {'id': 'wamid.early', 'status': 'read'})
        event._process()
        self.assertEqual(event.state, 'waiting')
        message = self.enqueue()
        message.provider_uid = 'wamid.early'
        event._process()
        self.assertEqual(event.state, 'done')
        self.assertEqual(message.state, 'read')

    def test_callback_data_matches_unknown(self):
        message = self.enqueue()
        message.state = 'unknown'
        event = self.Event._receive(self.account, 'status', {'id': 'wamid.recovered', 'status': 'delivered',
                                                         'biz_opaque_callback_data': 'request1'})
        event._process()
        self.assertEqual(message.provider_uid, 'wamid.recovered')
        self.assertEqual(message.state, 'delivered')

    def test_duplicate_inbound(self):
        event = self.event()
        self.assertEqual(self.event(), event)
        self.Event._cron_process()
        self.Event._cron_process()
        self.assertEqual(event.state, 'done')
        self.assertEqual(self.Message.search_count([('provider_uid', '=', 'in1')]), 1)

    def test_optout_persists_after_question(self):
        self.event(content={'body': 'STOP promotions!'})._process()
        consent = self.env['qing.whatsapp.consent'].search([('conversation_id', '=', self.conversation.id)], limit=1)
        self.assertEqual(consent.state, 'revoked')
        self.event(uid='question', content={'body': 'Where is my order?'})._process()
        self.assertEqual(consent.state, 'revoked')
        self.template.category = 'MARKETING'
        with self.assertRaises(UserError):
            self.Message.with_user(self.operator)._enqueue(self.conversation, 'template', [], request_key='marketing', template=self.template)
        self.enqueue('service_question')

    def test_marketing_needs_evidence(self):
        self.template.category = 'MARKETING'
        with self.assertRaises(UserError):
            self.Message.with_user(self.operator)._enqueue(self.conversation, 'template', [], request_key='marketing', template=self.template)
        self.env['qing.whatsapp.consent'].create({'conversation_id': self.conversation.id,
            'purpose': 'marketing', 'state': 'granted', 'evidence': 'Customer consent form'})
        message = self.Message.with_user(self.operator)._enqueue(self.conversation, 'template', [], request_key='marketing', template=self.template)
        self.assertEqual(message.purpose, 'marketing')

    def test_permission_boundaries(self):
        message = self.enqueue()
        with self.assertRaises(AccessError):
            message.with_user(self.operator).write({'state': 'read'})
        with self.assertRaises(AccessError):
            self.account.with_user(self.operator).read(['access_token'])
        with self.assertRaises(AccessError):
            self.Message.with_user(self.visitor)._enqueue(self.conversation, 'text', {'body': 'bad'}, request_key='bad')
        with self.assertRaises(AccessError):
            self.conversation.with_user(self.operator).write({'last_customer_at': fields.Datetime.now()})
        with self.assertRaises(AccessError):
            self.template.with_user(self.manager).write({'status': 'APPROVED'})

    def test_other_company_and_unassigned_user(self):
        other_company = self.env['res.company'].create({'name': 'Other'})
        other = self.env['qing.whatsapp.account'].create({'name': 'Other WA', 'phone_uid': '54321', 'waba_uid': '111', 'company_id': other_company.id})
        with self.assertRaises(AccessError):
            other.with_user(self.manager).read(['name'])
        stranger = new_test_user(self.env, login='stranger_wa', groups='qing_whatsapp.group_user')
        with self.assertRaises(AccessError):
            self.account.with_user(stranger)._assert_operator()

    def test_immutable_account_identity(self):
        with self.assertRaises(ValidationError):
            self.account.write({'phone_uid': '999'})

    def test_cancel_only_before_send(self):
        message = self.enqueue()
        message.with_user(self.operator).action_cancel()
        self.assertEqual(message.state, 'cancelled')
        message.state = 'unknown'
        with self.assertRaises(UserError):
            message.with_user(self.operator).action_cancel()

    def test_template_edit_resets_approval(self):
        self.template.with_user(self.manager).write({'components_json': '[{"type":"BODY","text":"New text"}]'})
        self.assertEqual(self.template.status, 'DRAFT')

    def test_media_snapshot_and_integrity(self):
        attachment = self.env['ir.attachment'].create({'name': 'test.txt', 'raw': b'original', 'mimetype': 'text/plain',
            'res_model': self.conversation._name, 'res_id': self.conversation.id})
        message = self.Message.with_user(self.operator)._enqueue(self.conversation, 'document', {}, request_key='media', attachment=attachment)
        attachment.raw = b'changed'
        self.assertEqual(message.attachment_id.raw.content, b'original')
        message.attachment_id.raw = b'tampered'
        with self.assertRaises(CloudError):
            message._send_cloud()

    def test_bad_event_does_not_block_good(self):
        bad = self.Event._receive(self.account, 'message', {'id': 'bad'})
        good = self.event()
        self.Event._cron_process()
        self.assertEqual(bad.state, 'retry')
        self.assertEqual(good.state, 'done')

    def test_unknown_message_preserved(self):
        event = self.event(kind='future_type', content={'example': True})
        event._process()
        message = self.Message.search([('provider_uid', '=', 'in1')])
        self.assertEqual(message.kind, 'other')
        self.assertTrue(message.payload)

    def test_order_extension_is_pending_not_order_creation(self):
        orders_before = self.env['sale.order'].search_count([]) if 'sale.order' in self.env.registry else 0
        event = self.event(kind='order', content={'catalog_id': 'cat', 'product_items': [{'product_retailer_id': 'SKU1', 'quantity': 2}]})
        event._process()
        commerce = self.env['qing.whatsapp.commerce.event'].search([('event_id', '=', event.id)])
        self.assertEqual(commerce.state, 'pending')
        self.assertEqual(commerce.payload['catalog_id'], 'cat')
        if 'sale.order' in self.env.registry:
            self.assertEqual(self.env['sale.order'].search_count([]), orders_before)

    def test_commerce_builders_and_same_outbox(self):
        payload = product_message('cat', 'SKU1')
        message = self.conversation.with_user(self.operator)._queue_commerce_message(payload, 'product')
        self.assertEqual(message.payload['interactive']['action']['product_retailer_id'], 'SKU1')
        self.assertEqual(message.state, 'queued')
        with self.assertRaises(ValueError):
            shop_link('http://shop.example', 'Shop', 'Open')
        with self.assertRaises(ValueError):
            product_list('cat', 'Title', 'Body', [])
        self.assertEqual(flow_message('flow1', 'token', 'Start', 'WELCOME', 'Welcome')['type'], 'flow')

    def test_signature(self):
        raw = b'{"entry":[]}'
        signature = 'sha256=' + hmac.new(b'secret', raw, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature(raw, signature, 'secret'))
        self.assertFalse(valid_signature(raw + b' ', signature, 'secret'))
        self.assertFalse(valid_signature(raw, None, 'secret'))

    def test_transport_uncertainty_and_throttle(self):
        import requests
        client = self.account._client()
        with patch('requests.request', side_effect=requests.Timeout()):
            with self.assertRaises(CloudError) as caught:
                client.send({})
            self.assertTrue(caught.exception.uncertain)
        with patch('requests.request') as call:
            call.return_value.ok = False
            call.return_value.status_code = 429
            call.return_value.json.return_value = {'error': {'code': 130429}}
            with self.assertRaises(CloudError) as caught:
                client.send({})
            self.assertTrue(caught.exception.retryable)
            self.assertFalse(caught.exception.uncertain)

    def test_form_composer_and_template_parameters(self):
        from odoo.tests import Form
        with Form(self.env['qing.whatsapp.compose'].with_user(self.operator)) as form:
            form.account_id = self.account
            form.number = self.conversation.number
            form.body = 'Form message'
        wizard = form.record
        result = wizard.action_send()
        self.assertEqual(result['res_id'], self.conversation.id)
        self.template.components_json = json.dumps([{'type': 'BODY', 'text': 'Hello {{1}}, order {{2}}'}])
        wizard = self.env['qing.whatsapp.compose'].with_user(self.operator).create({
            'account_id': self.account.id, 'number': self.conversation.number, 'kind': 'template',
            'template_id': self.template.id, 'parameter_values': 'Customer\nSO001'})
        self.assertEqual(wizard._template_parameters()[0]['parameters'][1]['text'], 'SO001')

    def test_unknown_cannot_be_manually_retried(self):
        message = self.enqueue()
        message.state = 'unknown'
        with self.assertRaises(UserError):
            message.with_user(self.operator).action_retry()

    def test_delayed_stop_is_not_lost(self):
        event = self.event(content={'body': 'STOP'})
        payload = dict(event.payload, timestamp=str(int((fields.Datetime.now() - timedelta(hours=1)).timestamp())))
        event.payload = payload
        event._process()
        consent = self.env['qing.whatsapp.consent'].search([('conversation_id', '=', self.conversation.id)], limit=1)
        self.assertEqual(consent.state, 'revoked')

    def test_template_media_and_file_upload(self):
        import base64
        self.template.components_json = json.dumps([{'type': 'HEADER', 'format': 'DOCUMENT'}])
        wizard = self.env['qing.whatsapp.compose'].with_user(self.operator).create({
            'account_id': self.account.id, 'number': self.conversation.number, 'kind': 'template',
            'template_id': self.template.id, 'upload_data': base64.b64encode(b'file content').decode(), 'upload_name': 'report.txt'})
        wizard.action_send()
        message = self.Message.search([('request_key', '=', wizard.request_key)])
        self.assertEqual(message.media_kind, 'document')
        with patch.object(CloudAPI, 'upload', return_value='media1'), patch.object(CloudAPI, 'send', return_value='wamid.media') as send:
            message._send_cloud()
        sent = send.call_args.args[0]
        self.assertEqual(sent['template']['components'][0]['parameters'][0]['document']['id'], 'media1')

    def test_queued_template_definition_is_frozen(self):
        message = self.Message.with_user(self.operator)._enqueue(self.conversation, 'template', [], request_key='frozen', template=self.template)
        self.template.components_json = '[{"type":"BODY","text":"Changed externally"}]'
        with self.assertRaises(UserError):
            message._preflight()
