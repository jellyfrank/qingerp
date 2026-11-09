import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOpenAi(TransactionCase):

    def test_image_is_sent_as_responses_input_part(self):
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': True,
            'endpoint': 'https://api.openai.com/v1/responses', 'model': 'gpt-4.1-mini',
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}],
            }
            service._complete_text('Describe', provider='openai', config=config,
                                   image={'mimetype': 'image/png', 'data': 'YWJj'})
        content = post.call_args.kwargs['json']['input'][0]['content']
        self.assertEqual(content[0], {'type': 'input_text', 'text': 'Describe'})
        self.assertEqual(content[1]['image_url'], 'data:image/png;base64,YWJj')

    def test_saved_settings_and_responses_request(self):
        settings = self.env['res.config.settings'].create({
            'qing_openai_api_key': 'test-key',
            'qing_openai_enabled': True,
            'qing_openai_endpoint': 'https://api.openai.com/v1/responses',
            'qing_openai_model': 'gpt-4.1-mini',
        })
        settings.execute()
        service = self.env['qing.ai.service']
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'model': 'gpt-4.1-mini',
                'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': 'OK'}
                ]}],
                'usage': {'total_tokens': 4},
            }
            result = service._complete_text('Hi', provider='openai')
            self.assertEqual(result['text'], 'OK')
            self.assertEqual(post.call_args.kwargs['json']['input'], 'Hi')
            self.assertFalse(post.call_args.kwargs['json']['store'])
            self.assertEqual(post.call_args.kwargs['json']['max_output_tokens'], 4096)
            self.assertEqual(settings.action_test_qing_openai()['tag'], 'display_notification')

    def test_incomplete_response_is_marked_as_truncated(self):
        from odoo.exceptions import UserError
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
            'endpoint': 'https://api.openai.com/v1/responses', 'model': 'gpt-5-mini',
        }
        incomplete = {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = dict(incomplete, output=[
                {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Partial'}]},
            ])
            result = service._complete_text('Hi', provider='openai', config=config)
            self.assertTrue(result['truncated'])
            post.return_value.json.return_value = dict(incomplete, output=[{'type': 'reasoning'}])
            with self.assertRaises(UserError) as error:
                service._complete_text('Hi', provider='openai', config=config)
        self.assertIn('output token limit', str(error.exception))

    def test_system_instructions_and_json_mode(self):
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
            'endpoint': 'https://api.openai.com/v1/responses', 'model': 'gpt-4.1-mini',
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {'output': [
                {'type': 'message', 'content': [{'type': 'output_text', 'text': '{}'}]},
            ]}
            service._complete_text('Data', provider='openai', config=config,
                                   system='Rules', json_mode=True)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['instructions'], 'Rules')
        self.assertEqual(payload['input'], 'Data')
        self.assertEqual(payload['text'], {'format': {'type': 'json_object'}})

    def test_streaming_and_function_call_output(self):
        from unittest.mock import MagicMock
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
            'endpoint': 'https://api.openai.com/v1/responses', 'model': 'gpt-4.1-mini',
        }
        stream = MagicMock()
        stream.status_code = 200
        events = [
            {'type': 'response.output_text.delta', 'delta': 'Hel'},
            {'type': 'response.output_text.delta', 'delta': 'lo'},
            {'type': 'response.completed', 'response': {'model': 'gpt-4.1-mini', 'status': 'completed',
                                                       'usage': {'input_tokens': 3, 'output_tokens': 2}}},
        ]
        stream.iter_lines.return_value = iter(
            [b'event: x'] + [b'data: ' + json.dumps(event).encode() for event in events])
        deltas = []
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post', return_value=stream) as post:
            result = service._complete_text('Hi', provider='openai', config=config, on_delta=deltas.append)
        self.assertTrue(post.call_args.kwargs['json']['stream'])
        self.assertEqual(deltas, ['Hel', 'lo'])
        self.assertEqual((result['text'], result['usage']['output_tokens']), ('Hello', 2))
        tools = [{'name': 'find_customers', 'description': 'Find', 'parameters': {'type': 'object'}}]
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {'output': [
                {'type': 'function_call', 'name': 'find_customers', 'arguments': '{"search": "A"}'},
            ]}
            result = service._complete_text('Find A', provider='openai', config=config, tools=tools)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['tools'][0]['type'], 'function')
        self.assertFalse(payload['parallel_tool_calls'])
        self.assertEqual(result['tool_call'], {'name': 'find_customers', 'arguments': {'search': 'A'}})
