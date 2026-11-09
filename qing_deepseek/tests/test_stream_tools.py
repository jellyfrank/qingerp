"""DeepSeek streaming and native function calling at the HTTP boundary."""

import json
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

POST = 'odoo.addons.qing_ai.models.qing_ai_service.requests.post'
CONFIG = {
    'api_key': 'test-key', 'enabled': True, 'image_enabled': False,
    'endpoint': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-chat',
}


def sse(*events):
    response = MagicMock()
    response.status_code = 200
    lines = [b': keep-alive', b'']
    for event in events:
        lines.append(b'data: ' + json.dumps(event, ensure_ascii=False).encode('utf-8'))
        lines.append(b'')
    lines.append(b'data: [DONE]')
    response.iter_lines.return_value = iter(lines)
    return response


@tagged('post_install', '-at_install')
class TestDeepSeekStreamTools(TransactionCase):

    def test_streamed_answer_forwards_utf8_deltas_and_usage(self):
        service = self.env['qing.ai.service']
        deltas = []
        events = [
            {'model': 'deepseek-chat', 'choices': [{'delta': {'content': '您好，'}}]},
            {'choices': [{'delta': {'reasoning_content': 'hidden'}}]},
            {'choices': [{'delta': {'content': '感谢支持！'}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 9, 'completion_tokens': 4}},
        ]
        with patch(POST, return_value=sse(*events)) as post:
            result = service._complete_text('Hi', provider='deepseek', config=CONFIG,
                                            system='Rules', on_delta=deltas.append)
        payload = post.call_args.kwargs['json']
        self.assertTrue(payload['stream'])
        self.assertTrue(post.call_args.kwargs['stream'])
        self.assertEqual(deltas, ['您好，', '感谢支持！'])
        self.assertEqual(result['text'], '您好，感谢支持！')
        self.assertEqual(result['usage'], {'prompt_tokens': 9, 'completion_tokens': 4})
        self.assertFalse(result['truncated'])

    def test_native_tool_call_is_requested_and_parsed(self):
        service = self.env['qing.ai.service']
        tools = [{'name': 'find_customers', 'description': 'Find customers',
                  'parameters': {'type': 'object', 'properties': {'search': {'type': 'string'}}}}]
        reply = {'choices': [{'message': {'content': None, 'tool_calls': [{
            'id': 'call_1', 'type': 'function',
            'function': {'name': 'find_customers', 'arguments': '{"search": "王五"}'},
        }]}, 'finish_reason': 'tool_calls'}]}
        with patch(POST) as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = reply
            result = service._complete_text('Find Wang Wu', provider='deepseek', config=CONFIG,
                                            system='Rules', tools=tools, on_delta=lambda delta: None)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['tools'][0], {'type': 'function', 'function': tools[0]})
        self.assertEqual(payload['tool_choice'], 'auto')
        self.assertFalse(payload['stream'], 'Planner tool calls are never streamed.')
        self.assertNotIn('response_format', payload)
        self.assertEqual(result['tool_call'], {'name': 'find_customers', 'arguments': {'search': '王五'}})
        self.assertTrue(service._supports_native_tools('deepseek'))

    def test_malformed_tool_arguments_are_reported_as_unparsed(self):
        service = self.env['qing.ai.service']
        reply = {'choices': [{'message': {'tool_calls': [{
            'function': {'name': 'find_customers', 'arguments': '{"search": '},
        }]}}]}
        with patch(POST) as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = reply
            result = service._complete_text('x', provider='deepseek', config=CONFIG,
                                            tools=[{'name': 'find_customers', 'description': '',
                                                    'parameters': {'type': 'object'}}])
        self.assertEqual(result['tool_call'], {'name': 'find_customers', 'arguments': None})
