"""Provider error diagnostics, bounded retries and output budget handling."""

from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

POST = 'odoo.addons.qing_ai.models.qing_ai_service.requests.post'
SLEEP = 'odoo.addons.qing_ai.models.qing_ai_service.time.sleep'
URL = 'https://provider.example.com/v1/chat'


def response(status, payload=None, headers=None):
    item = MagicMock()
    item.status_code = status
    item.headers = headers or {}
    if payload is None:
        item.json.side_effect = ValueError('no json')
    else:
        item.json.return_value = payload
    return item


@tagged('post_install', '-at_install')
class TestQingAiProviderResilience(TransactionCase):

    def setUp(self):
        super().setUp()
        self.service = self.env['qing.ai.service']

    def test_rate_limit_is_retried_with_retry_after(self):
        ok = response(200, {'choices': [{'message': {'content': 'OK'}}]})
        limited = response(429, {'error': {'type': 'rate_limit_exceeded'}}, {'Retry-After': '3'})
        with patch(POST, side_effect=[limited, ok]) as post, patch(SLEEP) as sleep:
            data = self.service._post_json(URL, {}, 'secret')
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(3)
        self.assertEqual(data['choices'][0]['message']['content'], 'OK')

    def test_quota_exhaustion_is_reported_without_retry(self):
        exhausted = response(429, {'error': {
            'message': 'You exceeded your quota, key sk-secret', 'code': 'insufficient_quota',
        }})
        with patch(POST, return_value=exhausted) as post, patch(SLEEP) as sleep:
            with self.assertRaises(UserError) as error:
                self.service._post_json(URL, {}, 'secret')
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()
        message = str(error.exception)
        self.assertIn('429', message)
        self.assertIn('insufficient_quota', message)
        self.assertNotIn('sk-secret', message)

    def test_server_errors_stop_after_bounded_retries(self):
        unavailable = response(503, {'error': {'status': 'UNAVAILABLE'}})
        with patch(POST, return_value=unavailable) as post, patch(SLEEP):
            with self.assertRaises(UserError) as error:
                self.service._post_json(URL, {}, 'secret')
        self.assertEqual(post.call_count, 3)
        self.assertIn('UNAVAILABLE', str(error.exception))

    def test_authentication_error_is_actionable_and_hides_free_text(self):
        denied = response(401, {'error': {'message': 'Incorrect key sk-abc', 'type': 'invalid_request_error',
                                          'code': 'invalid_api_key'}})
        with patch(POST, return_value=denied) as post, patch(SLEEP):
            with self.assertRaises(UserError) as error:
                self.service._post_json(URL, {}, 'secret')
        self.assertEqual(post.call_count, 1)
        self.assertIn('API key', str(error.exception))
        self.assertIn('invalid_api_key', str(error.exception))
        self.assertNotIn('sk-abc', str(error.exception))

    def test_free_text_error_codes_are_not_echoed(self):
        bad = response(400, {'error': {'code': 'model gpt-x does not exist for key sk-1'}})
        with patch(POST, return_value=bad), patch(SLEEP):
            with self.assertRaises(UserError) as error:
                self.service._post_json(URL, {}, 'secret')
        self.assertNotIn('sk-1', str(error.exception))
        self.assertIn('model name', str(error.exception))

    def test_connection_errors_are_retried_but_read_timeouts_are_not(self):
        ok = response(200, {'ok': True})
        with patch(POST, side_effect=[requests.ConnectionError(), ok]) as post, patch(SLEEP):
            self.assertEqual(self.service._post_json(URL, {}, 'secret'), {'ok': True})
        self.assertEqual(post.call_count, 2)
        with patch(POST, side_effect=requests.ReadTimeout()) as post, patch(SLEEP):
            with self.assertRaises(UserError) as error:
                self.service._post_json(URL, {}, 'secret')
        self.assertEqual(post.call_count, 1)
        self.assertIn('60', str(error.exception))

    def test_output_budget_setting_is_clamped(self):
        parameters = self.env['ir.config_parameter'].sudo()
        self.assertEqual(self.service._default_max_output_tokens(), 4096)
        parameters.set_int('qing_ai.max_output_tokens', 300)
        self.assertEqual(self.service._default_max_output_tokens(), 300)
        parameters.set_int('qing_ai.max_output_tokens', 99999)
        self.assertEqual(self.service._default_max_output_tokens(), 8192)
        parameters.set_int('qing_ai.max_output_tokens', 10)
        self.assertEqual(self.service._default_max_output_tokens(), 256)

    def test_empty_truncated_answer_explains_the_output_budget(self):
        with self.assertRaises(UserError) as error:
            self.service._raise_empty_text(truncated=True)
        self.assertIn('output token limit', str(error.exception))
        with self.assertRaises(UserError) as error:
            self.service._raise_empty_text()
        self.assertIn('no text', str(error.exception))
