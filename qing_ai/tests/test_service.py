from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiService(TransactionCase):

    def test_usage_summary_normalizes_exact_provider_counters(self):
        service = self.env['qing.ai.service']
        self.assertEqual(service._usage_summary({
            'input_tokens': 12,
            'output_tokens': 7,
            'total_tokens': 19,
            'output_tokens_details': {'reasoning_tokens': 3},
        }), {
            'input_tokens': 12,
            'output_tokens': 7,
            'reasoning_tokens': 3,
            'total_tokens': 19,
        })
        self.assertEqual(service._usage_summary({
            'promptTokenCount': 10,
            'candidatesTokenCount': 4,
            'thoughtsTokenCount': 2,
            'totalTokenCount': 16,
        }), {
            'input_tokens': 10,
            'output_tokens': 4,
            'reasoning_tokens': 2,
            'total_tokens': 16,
        })

    def test_usage_summary_does_not_estimate_missing_counters(self):
        service = self.env['qing.ai.service']
        self.assertEqual(service._usage_summary({'input_tokens': 5}), {'input_tokens': 5})
        self.assertEqual(service._usage_summary({
            'total_tokens': True, 'output_tokens': -1, 'ignored': 8,
        }), {})

    def test_default_without_provider_is_rejected(self):
        service = self.env['qing.ai.service']
        with self.assertRaises(UserError):
            service._complete_text('Hello', provider='none')
        with self.assertRaises(UserError):
            service._complete_text('Hello', max_output_tokens=0)

    def test_http_validation_does_not_send_key(self):
        service = self.env['qing.ai.service']
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            with self.assertRaises(UserError):
                service._post_json('http://example.com/ai', {}, 'secret')
            post.assert_not_called()

    def test_http_error_does_not_disclose_provider_body(self):
        service = self.env['qing.ai.service']
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 401
            post.return_value.text = 'secret and prompt'
            with self.assertRaises(UserError) as error:
                service._post_json('https://example.com/ai', {}, 'secret')
            self.assertNotIn('secret', str(error.exception))
            self.assertNotIn('prompt', str(error.exception))
            self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_only_administrator_can_run_connection_test(self):
        user = new_test_user(self.env, login='qing-ai-employee', groups='base.group_user')
        service = self.env['qing.ai.service'].with_user(user)
        with self.assertRaises(AccessError):
            service._test_provider('none', {})
