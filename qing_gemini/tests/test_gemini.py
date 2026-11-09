import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGemini(TransactionCase):

    def test_image_is_sent_as_inline_data(self):
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': True,
            'endpoint': 'https://generativelanguage.googleapis.com/v1beta',
            'model': 'gemini-2.5-flash',
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'candidates': [{'content': {'parts': [{'text': 'OK'}]}}],
            }
            service._complete_text('Describe', provider='gemini', config=config,
                                   image={'mimetype': 'image/png', 'data': 'YWJj'})
        parts = post.call_args.kwargs['json']['contents'][0]['parts']
        self.assertEqual(parts[0], {'text': 'Describe'})
        self.assertEqual(parts[1]['inline_data'], {'mime_type': 'image/png', 'data': 'YWJj'})

    def test_saved_settings_and_generate_content_request(self):
        settings = self.env['res.config.settings'].create({
            'qing_gemini_api_key': 'test-key',
            'qing_gemini_enabled': True,
            'qing_gemini_endpoint': 'https://generativelanguage.googleapis.com/v1beta',
            'qing_gemini_model': 'gemini-2.5-flash',
        })
        settings.execute()
        service = self.env['qing.ai.service']
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'modelVersion': 'gemini-2.5-flash',
                'candidates': [{'content': {'parts': [{'text': 'OK'}]}}],
                'usageMetadata': {'totalTokenCount': 4},
            }
            result = service._complete_text('Hi', provider='gemini')
            self.assertEqual(result['text'], 'OK')
            self.assertIn('/models/gemini-2.5-flash:generateContent', post.call_args.args[0])
            self.assertEqual(post.call_args.kwargs['headers']['x-goog-api-key'], 'test-key')
            self.assertEqual(post.call_args.kwargs['json']['generationConfig']['maxOutputTokens'], 4096)
            self.assertEqual(settings.action_test_qing_gemini()['tag'], 'display_notification')

    def test_thinking_budget_exhaustion_is_explained(self):
        from odoo.exceptions import UserError
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
            'endpoint': 'https://generativelanguage.googleapis.com/v1beta',
            'model': 'gemini-2.5-flash',
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'candidates': [{'content': {'role': 'model'}, 'finishReason': 'MAX_TOKENS'}],
                'usageMetadata': {'thoughtsTokenCount': 4090},
            }
            with self.assertRaises(UserError) as error:
                service._complete_text('Hi', provider='gemini', config=config)
            self.assertIn('output token limit', str(error.exception))
            post.return_value.json.return_value = {
                'candidates': [{'content': {'parts': [{'text': 'Partial'}]}, 'finishReason': 'MAX_TOKENS'}],
            }
            result = service._complete_text('Hi', provider='gemini', config=config)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['text'], 'Partial')

    def test_system_instruction_and_json_mime_type(self):
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
            'endpoint': 'https://generativelanguage.googleapis.com/v1beta',
            'model': 'gemini-2.5-flash',
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {'candidates': [{'content': {'parts': [{'text': '{}'}]}}]}
            service._complete_text('Data', provider='gemini', config=config,
                                   system='Rules', json_mode=True)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['systemInstruction'], {'parts': [{'text': 'Rules'}]})
        self.assertEqual(payload['generationConfig']['responseMimeType'], 'application/json')
        self.assertEqual(payload['contents'][0]['parts'][0], {'text': 'Data'})

    def test_streaming_uses_sse_endpoint(self):
        from unittest.mock import MagicMock
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
            'endpoint': 'https://generativelanguage.googleapis.com/v1beta',
            'model': 'gemini-2.5-flash',
        }
        stream = MagicMock()
        stream.status_code = 200
        chunks = [
            {'candidates': [{'content': {'parts': [{'text': 'thinking', 'thought': True}, {'text': '你好'}]}}]},
            {'candidates': [{'content': {'parts': [{'text': '！'}]}, 'finishReason': 'STOP'}],
             'usageMetadata': {'totalTokenCount': 7}},
        ]
        stream.iter_lines.return_value = iter([b'data: ' + json.dumps(c).encode() for c in chunks])
        deltas = []
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post', return_value=stream) as post:
            result = service._complete_text('Hi', provider='gemini', config=config, on_delta=deltas.append)
        self.assertTrue(post.call_args.args[0].endswith(':streamGenerateContent?alt=sse'))
        self.assertEqual(deltas, ['你好', '！'])
        self.assertEqual(result['text'], '你好！')
        self.assertFalse(service._supports_native_tools('gemini'))
