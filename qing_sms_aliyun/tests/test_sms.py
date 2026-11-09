from datetime import datetime
from unittest.mock import Mock, patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.addons.qing_sms.tests.test_sms import SmsFixture
from ..hooks import migrate_legacy
from ..services.aliyun_adapter import AliyunAdapter
from ..services.aliyun_client import AliyunSmsClient

CLIENT = 'odoo.addons.qing_sms_aliyun.services.aliyun_client.'


@tagged('post_install', '-at_install')
class TestAliyunAdapter(SmsFixture):
    def setUp(self):
        super().setUp()
        self.account.write({'provider': 'aliyun', 'key': 'dummy', 'secret': 'dummy-secret'})
        self.binding.action_review()
        self.real_adapter = AliyunAdapter(self.account)

    def test_template_application_uses_create_and_read_only_query(self):
        application = self.env['qing.sms.template.application'].create({
            'name': 'Order notice', 'account_id': self.account.id, 'sign_id': self.sign.id,
            'content': '订单 ${order_no} 已发货',
            'variable_schema': [{'key': 'order_no', 'type': 'text', 'max_length': 30}],
            'provider_variable_rules': {'order_no': 'characterWithNumber'}})
        application.action_queue()
        with patch.object(self.real_adapter.client, 'call', return_value={
            'Code': 'OK', 'TemplateCode': 'SMS_NEW', 'OrderId': 'audit-1', 'RequestId': 'request-1'}) as call:
            result = self.real_adapter.create_template(application)
        self.assertEqual(result['state'], 'pending')
        self.assertEqual(result['remote_code'], 'SMS_NEW')
        self.assertEqual(call.call_args.args[0], 'CreateSmsTemplate')
        self.assertEqual(call.call_args.kwargs['TemplateRule'], '{"order_no":"characterWithNumber"}')
        application._write_internal({'remote_code': 'SMS_NEW', 'state': 'pending'})
        with patch.object(self.real_adapter.client, 'call', return_value={
            'Code': 'OK', 'TemplateCode': 'SMS_NEW', 'TemplateStatus': '1'}) as call:
            result = self.real_adapter.query_template(application)
        self.assertEqual(result['state'], 'approved')
        self.assertEqual(call.call_args.args[0], 'GetSmsTemplate')

    def test_provider_send_parameters_and_acceptance(self):
        message = self.enqueue()
        with patch(CLIENT + 'AliyunSmsClient.sendSms', return_value={'Code': 'OK', 'BizId': 'biz', 'RequestId': 'req'}) as send:
            result = self.real_adapter.send(message)
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(send.call_args.args, ('13800138000', '测试签名', 'T1', {'order_no': 'SO001'}))
        self.assertEqual(send.call_args.kwargs['outid'], message.sms_uuid)

    def test_ambiguous_acceptance_and_explicit_rejection(self):
        message = self.enqueue()
        for response, state in [({'Code': 'OK'}, 'unknown'), ({'Code': 'isp.SYSTEM_ERROR'}, 'unknown'),
                                ({'Code': 'isv.TEMPLATE_ILLEGAL'}, 'failed')]:
            with patch(CLIENT + 'AliyunSmsClient.sendSms', return_value=response):
                self.assertEqual(self.real_adapter.send(message)['state'], state)

    def test_transport_uses_https_timeout_no_redirects_and_utc(self):
        client = AliyunSmsClient('key', 'secret')
        with patch(CLIENT + 'requests.get') as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {'Code': 'OK'}
            client.sendSms('13800138000', '测试', 'T1', {'code': '1234'})
        self.assertTrue(get.call_args.args[0].startswith('https://dysmsapi.aliyuncs.com/'))
        self.assertEqual(get.call_args.kwargs['timeout'], (5, 20))
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertIn('SignatureNonce=', get.call_args.args[0])
        self.assertNotIn('None', get.call_args.args[0])

    def test_transport_malformed_and_http_errors(self):
        client = AliyunSmsClient('key', 'secret')
        with patch(CLIENT + 'requests.get') as get:
            get.return_value.status_code = 400
            get.return_value.json.return_value = {'Code': 'ForbiddenAction'}
            self.assertEqual(client.call('GetSmsSign', SignName='test')['Code'], 'ForbiddenAction')
            get.return_value.status_code = 200
            get.return_value.json.return_value = []
            with self.assertRaises(ValueError):
                client.call('GetSmsSign', SignName='test')

    def test_snapshot_reads_latest_detail_and_normalizes_status(self):
        def call(action, **params):
            data = {
                'QuerySmsSignList': {'Code': 'OK', 'TotalCount': 1, 'SmsSignList': [{'SignName': '测试'}]},
                'GetSmsSign': {'Code': 'OK', 'SignStatus': 1},
                'QuerySmsTemplateList': {'Code': 'OK', 'TotalCount': 1, 'SmsTemplateList': [{'TemplateCode': 'T1', 'TemplateType': 2}]},
                'GetSmsTemplate': {'Code': 'OK', 'TemplateName': '订单', 'TemplateContent': '订单 ${order_no}',
                                  'TemplateStatus': '1', 'TemplateType': '1', 'RelatedSignName': '测试',
                                  'VariableAttribute': '{"order_no":"characterWithNumber"}'},
            }
            return data[action]
        with patch.object(self.real_adapter.client, 'call', side_effect=call):
            signs, templates = self.real_adapter.snapshot()
        self.assertEqual(signs[0]['audit_state'], 'approved')
        self.assertEqual(templates[0]['category'], 'notification')
        self.assertEqual(templates[0]['variables'], ['order_no'])
        self.assertEqual(templates[0]['sign_names'], ['测试'])

    def test_pagination_must_be_complete(self):
        pages = [{'Code': 'OK', 'TotalCount': 2, 'Rows': [{'id': 1}]},
                 {'Code': 'OK', 'TotalCount': 2, 'Rows': [{'id': 2}]}]
        with patch.object(self.real_adapter.client, 'call', side_effect=pages):
            self.assertEqual(len(self.real_adapter._list('Test', 'Rows')), 2)
        with patch.object(self.real_adapter.client, 'call', side_effect=[pages[0], {'Code': 'OK', 'TotalCount': 2, 'Rows': []}]):
            with self.assertRaises(ValueError):
                self.real_adapter._list('Test', 'Rows')

    def test_receipt_exact_identity_and_china_date(self):
        message = self.enqueue()
        message._write_internal({'state': 'unknown', 'submitted_at': datetime(2026, 9, 19, 17, 0)})
        response = {'Code': 'OK', 'TotalCount': 1, 'SmsSendDetailDTOs': {'SmsSendDetailDTO': [
            {'PhoneNum': '13800138000', 'OutId': message.sms_uuid, 'SendStatus': 3, 'ErrCode': 'DELIVERED'}]}}
        with patch.object(self.real_adapter.client, 'call', return_value=response) as call:
            result = self.real_adapter.query(message)
            self.assertEqual(call.call_args.kwargs['SendDate'], '20260920')
        self.assertEqual(result['state'], 'delivered')
        response['SmsSendDetailDTOs']['SmsSendDetailDTO'][0]['OutId'] = 'another-message'
        with patch.object(self.real_adapter.client, 'call', return_value=response):
            self.assertEqual(self.real_adapter.query(message)['state'], 'unknown')

    def test_receipt_incomplete_pages_never_confirm_delivery(self):
        message = self.enqueue()
        message._write_internal({'state': 'unknown', 'submitted_at': datetime(2026, 9, 19, 17, 0)})
        response = {'Code': 'OK', 'TotalCount': 5001, 'SmsSendDetailDTOs': {'SmsSendDetailDTO': [
            {'PhoneNum': '13800138000', 'OutId': message.sms_uuid, 'SendStatus': 3}]}}
        with patch.object(self.real_adapter.client, 'call', return_value=response):
            with self.assertRaises(ValueError):
                self.real_adapter.query(message)
        response['TotalCount'] = 2
        empty = {'Code': 'OK', 'TotalCount': 2, 'SmsSendDetailDTOs': {'SmsSendDetailDTO': []}}
        with patch.object(self.real_adapter.client, 'call', side_effect=[response, empty]):
            with self.assertRaises(ValueError):
                self.real_adapter.query(message)

    def test_legacy_migration_and_enqueue_compatibility(self):
        config = self.env['qing.sms.aliyun.config'].create({'name': 'Legacy', 'key': 'k', 'secret': 's', 'sign': '旧签名'})
        self.env.company.aliyun_sms_config_id = config
        legacy = self.env['qing.sms.aliyun.template'].create({'name': '旧订单', 'code': 'OLD1'})
        migrate_legacy(self.env)
        binding = legacy._get_binding(self.env.company)
        self.assertFalse(binding.account_id.enabled)
        self.assertEqual(binding.remote_template_id.audit_state, 'unknown')
        with self.assertRaises(UserError):
            legacy.send_sms('13800138000', {'order_no': 'SO001'})
        binding.account_id.enabled = True
        binding.account_id._apply_snapshot([{'remote_id': '旧签名', 'name': '旧签名', 'audit_state': 'approved'}],
            [{'remote_id': 'OLD1', 'name': '旧订单', 'content': '订单 ${order_no}', 'normalized_content': '订单 ${order_no}',
              'variables': ['order_no'], 'audit_state': 'approved'}])
        binding.action_prepare_variables()
        binding.action_review()
        result = legacy.with_user(self.sender).send_sms('13800138000', {'order_no': 'SO001'})
        self.assertEqual(result['Code'], 'QUEUED')
        migrate_legacy(self.env)
        self.assertEqual(legacy._get_binding(self.env.company), binding)
        self.adapter.send.assert_not_called()

    def test_legacy_pending_and_error_become_query_only(self):
        config = self.env['qing.sms.aliyun.config'].create({'name': 'Legacy', 'key': 'k', 'secret': 's', 'sign': '旧签名'})
        self.env.company.aliyun_sms_config_id = config
        legacy = self.env['qing.sms.aliyun.template'].create({'name': '旧订单', 'code': 'OLD2'})
        sms = self.env['sms.sms'].create({'number': '13800138000', 'body': '{}', 'service_provider': 'aliyun',
                                        'aliyun_template': legacy.id, 'state': 'error'})
        migrate_legacy(self.env)
        self.assertEqual(sms.state, 'process')
        self.assertEqual(sms.domestic_message_id.state, 'unknown')
        with self.assertRaises(UserError):
            sms.resend_failed()
        self.adapter.send.assert_not_called()
