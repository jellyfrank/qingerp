from datetime import timedelta
from unittest.mock import Mock, patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


class SmsFixture(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['qing.sms.account'].create({'name': 'Test account', 'enabled': True})
        cls.account._apply_snapshot([{'remote_id': 'test-sign', 'name': '测试签名', 'audit_state': 'approved'}],
            [{'remote_id': 'T1', 'name': '订单通知', 'content': '订单 ${order_no} 已发货',
              'normalized_content': '订单 ${order_no} 已发货', 'variables': ['order_no'],
              'audit_state': 'approved', 'category': 'notification'}])
        cls.sign = cls.account.sign_ids
        cls.remote = cls.account.template_ids
        cls.template = cls.env['sms.template'].create({'name': 'Order notification',
            'model_id': cls.env['ir.model']._get('res.partner').id, 'body': '订单通知'})
        cls.binding = cls.env['qing.sms.binding'].create({'name': 'Order', 'account_id': cls.account.id,
            'sign_id': cls.sign.id, 'remote_template_id': cls.remote.id, 'template_id': cls.template.id})
        cls.binding.action_prepare_variables()
        cls.binding.action_review()
        cls.sender = cls.env['res.users'].create({'name': 'SMS sender', 'login': 'sms-fixture-sender',
            'group_ids': [Command.set([cls.env.ref('qing_sms.group_sms_user').id])],
            'company_id': cls.env.company.id, 'company_ids': [Command.set(cls.env.company.ids)]})

    def setUp(self):
        super().setUp()
        self.adapter = Mock()
        self.adapter.validate_parameters.return_value = None
        self.adapter.send.return_value = {'state': 'accepted', 'code': 'OK', 'provider_id': 'biz-1'}
        self.adapter.query.return_value = {'state': 'delivered', 'code': 'OK'}
        patcher = patch.object(type(self.account), '_get_adapter', return_value=self.adapter)
        patcher.start()
        self.addCleanup(patcher.stop)

    def enqueue(self, key='event-1'):
        return self.binding.send_sms('13800138000', {'order_no': 'SO001'}, event_key=key)


@tagged('post_install', '-at_install')
class TestDomesticSms(SmsFixture):
    def test_enqueue_only_and_deduplication(self):
        first = self.enqueue()
        second = self.enqueue()
        self.assertEqual(first, second)
        self.assertEqual(first.body, '订单 SO001 已发货')
        self.assertEqual(first.number, '+8613800138000')
        self.assertEqual(first.sms_id.state, 'process')
        first.sms_id.send()
        self.adapter.send.assert_not_called()
        with self.assertRaises(UserError):
            self.binding.send_sms('13800138000', {'order_no': 'OTHER'}, event_key='event-1')

    def test_native_fallback_only_for_native_messages(self):
        domestic = self.enqueue().sms_id
        native = self.env['sms.sms'].create({'body': 'hello', 'number': '+14155552671'})
        with patch('odoo.addons.sms.models.sms_sms.SmsSms._send') as original:
            (domestic + native)._send()
            original.assert_called_once()
        self.adapter.send.assert_not_called()

    def test_invalid_parameters_before_external_call(self):
        for parameters in ({}, {'wrong': 'x'}, {'order_no': []}, {'order_no': ''},
                           {'order_no': 'x' * 100}, {'order_no': 'a\nb'}, {'order_no': float('nan')}):
            with self.assertRaises(UserError):
                self.binding.send_sms('13800138000', parameters)
        for number in ('+14155552671', '008613', '02112345678'):
            with self.assertRaises(UserError):
                self.binding.send_sms(number, {'order_no': 'SO001'})
        self.adapter.send.assert_not_called()

    def test_cloud_changes_require_review_and_preserve_snapshot(self):
        message = self.enqueue()
        self.remote._write_synced({'content': '新正文 ${order_no}', 'normalized_content': '新正文 ${order_no}'})
        self.assertTrue(self.binding.needs_review)
        with self.assertRaises(UserError):
            self.enqueue('event-2')
        self.binding.action_review()
        with self.assertRaises(UserError):
            message._preflight()
        self.assertEqual(message.body, '订单 SO001 已发货')

    def test_rejected_sign_and_account_switch_are_blocked(self):
        self.sign._write_synced({'audit_state': 'rejected'})
        with self.assertRaises(UserError):
            self.enqueue()
        self.sign._write_synced({'audit_state': 'approved'})
        self.account.key = 'new-cloud-account'
        with self.assertRaises(UserError):
            self.enqueue()

    def test_sign_template_account_consistency(self):
        other = self.env['qing.sms.account'].create({'name': 'Other'})
        foreign_sign = self.env['qing.sms.sign'].create({'account_id': other.id, 'remote_id': 'x', 'name': 'x'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.binding.sign_id = foreign_sign

    def test_sender_permissions_and_private_metadata(self):
        binding = self.binding.with_user(self.sender)
        message = binding.send_sms('13800138000', {'order_no': 'SO001'})
        self.assertEqual(message.state, 'queued')
        with self.assertRaises(AccessError):
            message.read(['parameters'])
        with self.assertRaises(AccessError):
            self.account.with_user(self.sender).read(['key', 'secret'])
        with self.assertRaises(AccessError):
            binding.action_review()
        with self.assertRaises(AccessError):
            message.write({'state': 'queued'})
        with self.assertRaises(AccessError):
            self.env['qing.sms.message'].create({})
        with self.assertRaises(AccessError):
            self.remote.write({'audit_state': 'approved'})
        with self.assertRaises(AccessError):
            self.binding.write({'review_hash': 'forged'})
        with self.assertRaises(AccessError):
            self.binding.with_user(self.env.ref('base.public_user')).send_sms('13800138000', {'order_no': 'SO001'})

    def test_company_isolation(self):
        other = self.env['res.company'].create({'name': 'Other SMS company'})
        account = self.env['qing.sms.account'].create({'name': 'Other', 'company_id': other.id})
        with self.assertRaises(AccessError):
            account.with_user(self.sender).read(['name'])
        message = self.enqueue()
        self.assertEqual(message.company_id, self.env.company)

    def test_field_mapping_uses_record_permissions(self):
        variable = self.binding.variable_ids
        variable.write({'source': 'field', 'field_path': 'name'})
        self.binding.action_review()
        partner = self.env['res.partner'].create({'name': 'Test Partner'})
        message = self.binding.with_user(self.sender).send_sms('13800138000', {}, record_id=partner.id)
        self.assertIn('Test Partner', message.body)
        variable.field_path = '__class__'
        self.binding.action_review()
        with self.assertRaises(UserError):
            self.binding.send_sms('13800138000', {}, record_id=partner.id)

    def test_sync_is_idempotent_and_atomic(self):
        signs = [{'remote_id': 'test-sign', 'name': '测试签名', 'audit_state': 'approved'}]
        templates = [{'remote_id': 'T1', 'name': '订单通知', 'audit_state': 'approved', 'content': 'v2'}]
        self.adapter.snapshot.return_value = (signs, templates)
        self.account.action_sync()
        self.account.action_sync()
        self.assertEqual(len(self.account.template_ids), 1)
        self.assertEqual(self.remote.content, 'v2')
        self.adapter.snapshot.side_effect = TimeoutError('secret-signed-url')
        self.account.action_sync()
        self.assertEqual(self.remote.content, 'v2')
        self.assertNotIn('secret-signed-url', self.account.sync_error)

    def test_partial_duplicate_sync_does_not_overwrite(self):
        self.adapter.snapshot.return_value = (
            [{'remote_id': 'test-sign', 'name': 'changed', 'audit_state': 'approved'}],
            [{'remote_id': 'T1', 'name': 'a'}, {'remote_id': 'T1', 'name': 'b'}])
        self.account.action_sync()
        self.assertEqual(self.sign.name, '测试签名')
        self.assertTrue(self.account.sync_error)

    def test_receipts_are_monotonic_and_survive_queue_deletion(self):
        message = self.enqueue()
        message._apply_result({'state': 'accepted', 'code': 'OK', 'provider_id': 'b1'}, 'submit')
        self.assertEqual(message.sms_id.state, 'pending')
        message._apply_result({'state': 'delivered', 'code': 'OK'}, 'query')
        message._apply_result({'state': 'accepted', 'code': 'OLD'}, 'query')
        self.assertEqual(message.state, 'delivered')
        self.assertEqual(message.sms_id.state, 'sent')
        sms = message.sms_id
        sms.unlink()
        self.assertTrue(message.exists())
        self.assertFalse(message.sms_id)
        self.assertEqual(len(message.attempt_ids), 2)

    def test_cancel_and_unknown_do_not_resend(self):
        message = self.binding.with_user(self.sender).send_sms(
            '13800138000', {'order_no': 'SO001'}, event_key='event-1')
        message.with_user(self.sender).action_cancel()
        self.assertEqual(message.state, 'canceled')
        with self.assertRaises(UserError):
            message.action_cancel()
        unknown = self.enqueue('unknown')
        unknown._apply_result({'state': 'unknown', 'code': 'TIMEOUT'}, 'submit')
        with self.assertRaises(UserError):
            unknown.sms_id.action_set_outgoing()
        with self.assertRaises(UserError):
            unknown.sms_id.resend_failed()
        unknown.sms_id.write({'state': 'outgoing'})
        unknown.sms_id.send()
        self.adapter.send.assert_not_called()

    def test_sensitive_content_hidden_and_cleared(self):
        self.remote._write_synced({'category': 'otp'})
        self.binding.action_review()
        message = self.enqueue()
        self.assertNotIn('SO001', message.body)
        self.assertNotIn('SO001', message.sms_id.body)
        message._apply_result({'state': 'accepted', 'code': 'OK'}, 'submit')
        self.assertFalse(message.parameters)

    def test_otp_expiry(self):
        self.remote._write_synced({'category': 'otp'})
        self.binding.action_review()
        message = self.enqueue()
        message._write_internal({'expires_at': fields.Datetime.now() - timedelta(minutes=1)})
        with self.assertRaises(UserError):
            message._preflight()

    def test_send_wizard_preview(self):
        wizard = self.env['qing.sms.send'].with_user(self.sender).create({'binding_id': self.binding.id,
                                                                         'number': '13800138000'})
        wizard._onchange_binding()
        wizard.parameter_ids.value = 'SO001'
        wizard.action_preview()
        self.assertEqual(wizard.preview, '订单 SO001 已发货')
        action = wizard.action_send()
        self.assertEqual(action['res_model'], 'qing.sms.message')
        self.assertFalse(wizard.parameter_ids)

    def test_native_composer_routes_without_iap(self):
        wizard = self.env['sms.composer'].with_user(self.sender).create({
            'composition_mode': 'numbers', 'numbers': '+8613800138000',
            'template_id': self.template.id, 'res_model': 'res.partner', 'body': 'ignored'})
        wizard._onchange_domestic_binding()
        wizard.domestic_value_ids.value = 'SO001'
        with patch('odoo.addons.sms.models.sms_sms.SmsSms._send') as native:
            messages = wizard._action_send_sms()
        native.assert_not_called()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages.body, '订单 SO001 已发货')
        self.assertEqual(messages.state, 'queued')

    def test_composer_binding_search_is_batched(self):
        Composer = self.env['sms.composer'].with_user(self.sender)
        Binding = self.env['qing.sms.binding']
        for count in (1, 50, 150):
            wizards = Composer.browse()
            for _index in range(count):
                wizards |= Composer.new({'template_id': self.template.id})
            original = type(Binding).search
            calls = []

            def counted_search(records, domain, *args, **kwargs):
                calls.append(domain)
                return original(records, domain, *args, **kwargs)

            with patch.object(type(Binding), 'search', counted_search):
                wizards._compute_domestic_binding()
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(w.domestic_binding_id == self.binding for w in wizards))

        native_template = self.template.copy({'name': 'No domestic binding'})
        wizard = Composer.new({'template_id': native_template.id})
        wizard._compute_domestic_binding()
        self.assertFalse(wizard.domestic_binding_id)
        empty = Composer.new({})
        empty._compute_domestic_binding()
        self.assertFalse(empty.domestic_binding_id)

    def test_native_schedule_is_durable(self):
        future = fields.Datetime.now() + timedelta(hours=1)
        wizard = self.env['sms.composer'].with_user(self.sender).create({
            'composition_mode': 'numbers', 'numbers': '+8613800138000', 'scheduled_date': future,
            'template_id': self.template.id, 'res_model': 'res.partner', 'body': 'ignored'})
        wizard._onchange_domestic_binding()
        wizard.domestic_value_ids.value = 'SO001'
        wizard.action_schedule_message()
        message = self.env['qing.sms.message'].search([('event_key', 'like', 'composer:' + wizard.domestic_event_key)])
        self.assertEqual(message.not_before, future)
        self.adapter.send.assert_not_called()

    def test_native_domestic_route_cannot_fall_back(self):
        wizard = self.env['sms.composer'].with_user(self.sender).create({
            'composition_mode': 'numbers', 'numbers': '+8613800138000',
            'template_id': self.template.id, 'res_model': 'res.partner', 'body': 'ignored'})
        wizard.domestic_binding_id = False
        with self.assertRaises(UserError), patch('odoo.addons.sms.models.sms_sms.SmsSms._send') as native:
            wizard._action_send_sms()
        native.assert_not_called()

    def test_native_blacklist_is_preserved(self):
        self.env['phone.blacklist'].create({'number': '+8613800138000'})
        wizard = self.env['sms.composer'].with_user(self.sender).create({
            'composition_mode': 'numbers', 'numbers': '+8613800138000', 'use_exclusion_list': True,
            'template_id': self.template.id, 'res_model': 'res.partner', 'body': 'ignored'})
        wizard._onchange_domestic_binding()
        wizard.domestic_value_ids.value = 'SO001'
        with self.assertRaises(UserError):
            wizard._action_send_sms()
        self.adapter.send.assert_not_called()

    def test_native_non_sms_user_can_still_use_iap(self):
        user = self.env['res.users'].create({'name': 'Native sender', 'login': 'native-sender',
            'group_ids': [Command.set([self.env.ref('base.group_user').id])]})
        template = self.template.copy({'name': 'Native only'})
        wizard = self.env['sms.composer'].with_user(user).create({'composition_mode': 'numbers',
            'numbers': '+14155552671', 'template_id': template.id, 'res_model': 'res.partner', 'body': 'hello'})
        self.assertFalse(wizard.domestic_binding_id)
        with patch('odoo.addons.sms.models.sms_sms.SmsSms._send') as native:
            wizard._action_send_sms()
        native.assert_called_once()

    def test_template_revision_is_immutable_and_idempotent(self):
        original = self.remote.revision_ids
        self.remote._write_synced({'last_sync_at': fields.Datetime.now()})
        self.assertEqual(self.remote.revision_ids, original)
        message = self.enqueue()
        self.remote._write_synced({'content': 'changed'})
        self.assertEqual(len(self.remote.revision_ids), 2)
        self.assertEqual(message.template_revision_id, original)
        self.assertEqual(original.content, '订单 ${order_no} 已发货')
        with self.assertRaises(AccessError):
            original.write({'content': 'forged'})

    def test_enabled_domestic_account_blocks_untemplated_cn_iap(self):
        local = self.env['sms.sms'].create({'number': '+8613800138000', 'body': 'Free text'})
        overseas = self.env['sms.sms'].create({'number': '+14155552671', 'body': 'Free text'})
        with patch('odoo.addons.sms.models.sms_sms.SmsSms._send') as native:
            (local + overseas)._send()
        self.assertEqual(local.state, 'error')
        native.assert_called_once()

    def test_send_form_saves_readonly_variable_keys(self):
        with Form(self.env['qing.sms.send'].with_user(self.sender).with_context(default_binding_id=self.binding.id)) as form:
            form.number = '13800138000'
            with form.parameter_ids.edit(0) as value:
                value.value = 'SO001'
        wizard = form.record
        self.assertEqual(wizard.parameter_ids.key, 'order_no')
        wizard.action_preview()
        self.assertEqual(wizard.preview, '订单 SO001 已发货')
