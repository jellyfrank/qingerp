from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

CONFIG = {
    'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
    'endpoint': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-reasoner',
}


@tagged('post_install', '-at_install')
class TestDeepSeek(TransactionCase):

    def test_vision_model_uses_image_content_blocks(self):
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': True,
            'endpoint': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-flash',
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'choices': [{'message': {'content': 'OK'}}],
            }
            service._complete_text('Describe', provider='deepseek', config=config,
                                   image={'mimetype': 'image/png', 'data': 'YWJj'})
        content = post.call_args.kwargs['json']['messages'][0]['content']
        self.assertEqual(content[0], {'type': 'text', 'text': 'Describe'})
        self.assertEqual(content[1]['image_url']['url'], 'data:image/png;base64,YWJj')

    def test_saved_settings_and_chat_request(self):
        settings = self.env['res.config.settings'].create({
            'qing_ai_default_provider': 'deepseek',
            'qing_deepseek_api_key': 'test-key',
            'qing_deepseek_enabled': True,
            'qing_deepseek_endpoint': 'https://api.deepseek.com/chat/completions',
            'qing_deepseek_model': 'deepseek-chat',
        })
        settings.execute()
        service = self.env['qing.ai.service']
        self.assertEqual(service._provider_config('deepseek')['api_key'], 'test-key')
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'model': 'deepseek-chat',
                'choices': [{'message': {'content': 'OK'}}],
                'usage': {'total_tokens': 4},
            }
            result = service._complete_text('Hi')
            self.assertEqual(result['text'], 'OK')
            self.assertEqual(result['usage']['total_tokens'], 4)
            self.assertEqual(post.call_args.kwargs['json']['messages'][0]['content'], 'Hi')
            self.assertEqual(post.call_args.kwargs['json']['max_tokens'], 4096)
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer test-key')
            self.assertEqual(settings.action_test_qing_deepseek()['tag'], 'display_notification')

    def test_length_finish_reason_marks_answer_as_truncated(self):
        service = self.env['qing.ai.service']
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'choices': [{'message': {'content': 'Partial'}, 'finish_reason': 'length'}],
            }
            result = service._complete_text('Hi', provider='deepseek', config=CONFIG)
            self.assertTrue(result['truncated'])
            post.return_value.json.return_value = {
                'choices': [{'message': {'content': '', 'reasoning_content': 'thinking'},
                             'finish_reason': 'length'}],
            }
            with self.assertRaises(UserError) as error:
                service._complete_text('Hi', provider='deepseek', config=CONFIG)
        self.assertIn('output token limit', str(error.exception))

    def test_system_and_json_mode_with_plain_text_fallback(self):
        from odoo.addons.qing_ai.models.qing_ai_service import ProviderHTTPError
        service = self.env['qing.ai.service']
        ok = {'choices': [{'message': {'content': '{"answer": "OK"}'}}]}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = ok
            service._complete_text('Data', provider='deepseek', config=CONFIG,
                                   system='Rules', json_mode=True)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['messages'][0], {'role': 'system', 'content': 'Rules'})
        self.assertEqual(payload['messages'][1]['content'], 'Data')
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        calls = []

        def fake_post(endpoint, payload, api_key, **kwargs):
            calls.append(payload)
            if 'response_format' in payload:
                raise ProviderHTTPError('rejected', 400)
            return ok

        with patch.object(type(service), '_post_json', side_effect=fake_post, autospec=False):
            result = service._complete_text('Data', provider='deepseek', config=CONFIG,
                                            system='Rules', json_mode=True)
        self.assertEqual(result['text'], '{"answer": "OK"}')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('response_format', calls[1])
