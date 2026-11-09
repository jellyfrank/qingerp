"""End-to-end planner behaviour with native function calling and answer streaming."""

import json
from unittest.mock import MagicMock, patch

from odoo.addons.qing_ai.models.qing_ai_service import ProviderHTTPError
from odoo.tests import TransactionCase, new_test_user, tagged

POST_JSON = 'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._post_json'
POST = 'odoo.addons.qing_ai.models.qing_ai_service.requests.post'


def tool_reply(name, arguments):
    return {'model': 'deepseek-chat', 'usage': {'prompt_tokens': 30, 'completion_tokens': 5},
            'choices': [{'message': {'content': '', 'tool_calls': [{
                'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)},
            }]}, 'finish_reason': 'tool_calls'}]}


def text_reply(text):
    return {'model': 'deepseek-chat', 'usage': {}, 'choices': [{'message': {'content': text}}]}


@tagged('post_install', '-at_install')
class TestQingAiNativePlanning(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Param = cls.env['ir.config_parameter'].sudo()
        Param.set_bool('qing_ai.deepseek.enabled', True)
        for key, value in (
            ('qing_ai.default_provider', 'deepseek'),
            ('qing_ai.deepseek.api_key', 'test-key'), ('qing_ai.deepseek.model', 'deepseek-chat'),
            ('qing_ai.deepseek.endpoint', 'https://api.deepseek.com/chat/completions'),
        ):
            Param.set_str(key, value)
        groups = 'base.group_user,sales_team.group_sale_salesman'
        if cls.env.ref('qing_partner.group_partner_manager', raise_if_not_found=False):
            groups += ',qing_partner.group_partner_manager'
        cls.user = new_test_user(cls.env, login='qing-ai-native-user', groups=groups)

    def _conversation(self):
        return self.env['qing.ai.conversation'].with_user(self.user).create({})

    def test_native_tool_call_stages_preview_with_remaining_steps(self):
        conversation = self._conversation()
        sent = []

        def fake_post(endpoint, payload, api_key, **kwargs):
            sent.append(payload)
            return tool_reply('create_customer', {
                'name': 'Native Wang Wu', 'phone': None,
                '_progress': 'Preparing the customer.', '_remaining': 'Create the quotation.',
            })

        opened = conversation.assistant_send('请创建客户 Native Wang Wu 并生成报价单', task='business',
                                             background=True)
        with patch(POST_JSON, side_effect=fake_post):
            reply = conversation.assistant_continue_run(opened['active_run']['id'], True)
        payload = sent[0]
        names = [tool['function']['name'] for tool in payload['tools']]
        self.assertIn('create_customer', names)
        self.assertIn('ask_user_question', names)
        create = next(tool for tool in payload['tools'] if tool['function']['name'] == 'create_customer')
        self.assertIn('_remaining', create['function']['parameters']['properties'])
        system = payload['messages'][0]['content']
        self.assertNotIn('Output exactly one JSON object', system)
        self.assertIn('ask_user_question', system)
        self.assertNotIn('arguments schema', system)
        self.assertNotIn('response_format', payload)
        self.assertEqual(len(reply['pending_operations']), 1)
        self.assertIn('Create the quotation.', reply['messages'][-1]['content'])
        self.assertEqual(conversation.pending_plan['remaining'], 'Create the quotation.')

    def test_ask_user_function_becomes_a_question(self):
        conversation = self._conversation()
        with patch(POST_JSON, return_value=tool_reply('ask_user_question', {'question': '报价单要哪些产品？'})):
            reply = conversation.assistant_send('请创建报价单', task='business')
        self.assertEqual(reply['messages'][-1]['content'], '报价单要哪些产品？')
        self.assertFalse(reply['pending_operations'])

    def test_rejected_tools_fall_back_to_json_planning(self):
        conversation = self._conversation()
        sent = []

        def fake_post(endpoint, payload, api_key, **kwargs):
            sent.append(payload)
            if 'tools' in payload:
                raise ProviderHTTPError('tools unsupported', 400)
            return text_reply(json.dumps({'answer': 'No matching quotation.'}))

        with patch(POST_JSON, side_effect=fake_post):
            reply = conversation.assistant_send('查询王五的报价单', task='business')
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1]['response_format'], {'type': 'json_object'})
        self.assertIn('Output exactly one JSON object', sent[1]['messages'][0]['content'])
        self.assertEqual(reply['messages'][-1]['content'], 'No matching quotation.')

    def test_answer_first_reply_streams_without_the_route_token(self):
        conversation = self._conversation()
        pushed = []
        Conversation = type(conversation)
        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = iter([
            b'data: ' + json.dumps({'choices': [{'delta': {'content': piece}}]}, ensure_ascii=False).encode()
            for piece in ('感谢', '您的支持！')
        ] + [b'data: [DONE]'])
        with patch(POST, return_value=response), patch.object(
                Conversation, '_push_stream', autospec=True,
                side_effect=lambda record, text, done=False: pushed.append(text)):
            reply = conversation.assistant_send('写一句感谢语', stream=True)
        self.assertEqual(reply['messages'][-1]['content'], '感谢您的支持！')
        self.assertTrue(pushed, 'Partial answers must be pushed to the browser.')
        self.assertTrue(pushed[0].startswith('感谢'))

        conversation = self._conversation()
        publish = conversation._stream_publisher(hide_route_token=True)
        with patch.object(type(conversation), '_push_stream') as push:
            for piece in ('[[', 'ER', 'P]]'):
                publish(piece)
        push.assert_not_called()

    def test_push_stream_targets_the_owner_bus_channel(self):
        conversation = self._conversation()
        with patch('odoo.addons.bus.models.bus_listener_mixin.BusListenerMixin._bus_send',
                   autospec=True) as bus_send:
            conversation._push_stream('partial')
        record, notification, payload = bus_send.call_args.args
        self.assertEqual(record, self.user)
        self.assertEqual(notification, 'qing_ai/stream')
        self.assertEqual(payload, {'conversation_id': conversation.id, 'text': 'partial', 'done': False})
