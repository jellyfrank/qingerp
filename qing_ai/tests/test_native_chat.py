import json
from unittest.mock import patch

from markupsafe import Markup

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

COMPLETE = 'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text'


def _result(payload, input_tokens=10):
    return {
        'text': json.dumps(payload), 'model': 'mock-model',
        'usage': {'input_tokens': input_tokens, 'output_tokens': 2, 'total_tokens': input_tokens + 2},
    }


@tagged('post_install', '-at_install')
class TestQingAiNativeChat(TransactionCase):
    """The navbar AI button opens a native chat window that the browser advances step by step."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login='qing-ai-native-chat', groups='base.group_user,base.group_partner_manager',
        )
        cls.other = new_test_user(cls.env, login='qing-ai-native-other', groups='base.group_user')
        cls.model = cls.env['ir.model']._get('res.partner.category')
        cls.fields = cls.model.field_id.filtered(lambda field: field.name in ('name', 'color'))
        cls.skill = cls.env['qing.ai.skill'].create({'name': 'Manage Tags'})
        cls.agent = cls.env['qing.ai.agent'].create({
            'name': 'Tag Copilot', 'skill_ids': [Command.link(cls.skill.id)],
        })
        cls.count_tool = cls._tool('count')
        cls.create_tool = cls._tool('create')

    @classmethod
    def _tool(cls, operation):
        tool = cls.env['qing.ai.tool'].create({
            'name': 'Tags ' + operation, 'execution_type': 'orm', 'model_id': cls.model.id,
            'orm_operation': operation, 'orm_field_ids': [Command.set(cls.fields.ids)],
            'orm_write_field_ids': [Command.set(cls.fields.ids)] if operation == 'create' else [],
        })
        cls.skill.tool_ids = [Command.link(tool.id)]
        return tool

    def _launch(self, agent=None):
        Agent = self.env['qing.ai.agent'].with_user(self.user)
        result = Agent.assistant_launch_chat(agent.id if agent else False)
        return self.env['discuss.channel'].browse(result['channel_id']).with_user(self.user)

    def _post(self, channel, text):
        return channel.message_post(
            body=Markup('<p>%s</p>') % text, message_type='comment', subtype_xmlid='mail.mt_comment',
        )

    def _advance_until_done(self, channel):
        steps = 0
        while channel.qing_ai_advance()['running']:
            steps += 1
            self.assertLess(steps, 10, 'The reply must finish within a few steps.')
        return steps + 1

    def _agent_replies(self, channel):
        return self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'), ('res_id', '=', channel.id),
            ('author_id', '=', channel.sudo().qing_ai_identity_id.partner_id.id),
            ('message_type', '=', 'comment'),
        ], order='id')

    def test_launch_opens_the_private_chat_and_reuses_the_recent_agent(self):
        channel = self._launch(self.agent)
        self.assertEqual(channel.sudo().channel_type, 'chat')
        self.assertEqual(channel.sudo().qing_ai_agent_id, self.agent)
        self.assertEqual(channel.sudo().qing_ai_owner_id, self.user)
        self.assertEqual(self._launch(), channel, 'Without an agent the most recent AI chat is reused.')
        result = self.env['qing.ai.agent'].with_user(self.user).assistant_launch_chat(self.agent.id)
        channels = result['store_data'].as_dict()['discuss.channel']
        self.assertEqual(channels[0]['id'], channel.id)
        self.assertEqual(channels[0]['qing_ai_agent_id'], self.agent.id, 'The web client learns it is an AI chat.')
        with self.assertRaises(UserError):
            self.env['qing.ai.agent'].with_user(self.user).assistant_launch_chat(-1)

    def test_chat_window_steps_a_business_run_and_links_steps_and_usage(self):
        channel = self._launch(self.agent)
        self._post(channel, 'How many tags are there?')
        job = self.env['qing.ai.discuss.job'].search([('channel_id', '=', channel.id)])
        self.assertEqual(job.state, 'pending')
        plans = [
            _result({'tool': self.count_tool.key, 'arguments': {}, 'progress': 'Counting the tags.'}, 100),
            _result({'answer': 'There are some tags.', 'progress': 'Answering.'}, 40),
        ]
        with patch(COMPLETE, side_effect=plans) as complete:
            steps = self._advance_until_done(channel)
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(steps, 3, 'Start the run, then one call per planner round.')
        self.assertEqual(job.state, 'done')
        reply = self._agent_replies(channel)
        self.assertEqual(len(reply), 1)
        self.assertIn('There are some tags.', reply.body)
        self.assertTrue(reply.qing_ai_message_id)
        meta = reply._qing_ai_store_meta()
        self.assertEqual(meta['model'], 'mock-model')
        self.assertEqual(meta['usage_summary']['input_tokens'], 140)
        self.assertEqual(meta['process_steps'][0]['summary'], 'Counting the tags.')
        self.assertEqual(meta['operations'], [])
        self.assertEqual(channel.qing_ai_advance(), {'running': False, 'progressed': False})

    def test_confirming_a_preview_in_the_chat_applies_it_and_removes_the_button(self):
        channel = self._launch(self.agent)
        self._post(channel, 'Create a tag named Native Blue')
        plans = [_result({
            'tool': self.create_tool.key, 'arguments': {'values': {'name': 'Native Blue'}},
            'progress': 'Preparing the tag.',
        })]
        with patch(COMPLETE, side_effect=plans):
            self._advance_until_done(channel)
        preview = self._agent_replies(channel)[-1]
        operations = preview._qing_ai_store_meta()['operations']
        self.assertEqual(len(operations), 1)
        Tag = self.env['res.partner.category']
        self.assertFalse(Tag.search([('name', '=', 'Native Blue')]))

        self.assertEqual(channel.qing_ai_confirm_operation(operations[0]['id']), {'running': True})
        self.assertEqual(len(Tag.search([('name', '=', 'Native Blue')])), 1)
        self._advance_until_done(channel)
        created = Tag.search([('name', '=', 'Native Blue')])
        done = self._agent_replies(channel)[-1]
        self.assertIn('Record ID: %s' % created.id, done.body)
        self.assertNotEqual(done, preview)
        self.assertEqual(preview._qing_ai_store_meta()['operations'], [])

    def test_new_topic_starts_a_fresh_conversation(self):
        channel = self._launch(self.agent)
        first = channel.sudo().qing_ai_conversation_id
        self.assertTrue(channel.qing_ai_new_topic())
        second = channel.sudo().qing_ai_conversation_id
        self.assertNotEqual(first, second)
        self.assertEqual(second.agent_id, self.agent)
        self.assertEqual(second.owner_id, self.user)
        notice = self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'), ('res_id', '=', channel.id),
            ('message_type', '=', 'notification'),
        ])
        self.assertEqual(len(notice), 1)
        self.assertFalse(self.env['qing.ai.discuss.job'].search([('channel_id', '=', channel.id)]))

    def test_only_the_owner_drives_the_chat(self):
        channel = self._launch(self.agent)
        for method, args in (('qing_ai_advance', ()), ('qing_ai_new_topic', ()),
                             ('qing_ai_confirm_operation', (1,))):
            with self.assertRaises(AccessError):
                getattr(channel.with_user(self.other), method)(*args)

    def test_a_reply_link_requires_the_agent_author_in_the_bound_chat(self):
        channel = self._launch(self.agent)
        answer = self.env['qing.ai.message'].sudo().create({
            'content': 'Hidden answer', 'conversation_id': channel.sudo().qing_ai_conversation_id.id,
            'role': 'assistant', 'state': 'done',
        })
        context = {'qing_ai_reply_message_id': answer.id}
        own = channel.with_context(**context).message_post(body='Mine', message_type='comment')
        self.assertFalse(own.sudo().qing_ai_message_id, 'A user cannot link an answer to their message.')
        forged = channel.sudo().with_context(**context).message_post(
            body='Forged', message_type='comment', author_id=self.user.partner_id.id,
        )
        self.assertFalse(forged.qing_ai_message_id, 'Only the agent identity may post linked replies.')
        reply = channel.sudo()._qing_ai_post_reply('Shown answer', answer=answer)
        self.assertEqual(reply.qing_ai_message_id, answer)

    def test_stream_notifications_name_the_chat(self):
        channel = self._launch(self.agent)
        conversation = channel.sudo()._qing_ai_conversation_env()
        pushed = []
        with patch('odoo.addons.qing_ai.models.qing_ai_conversation._push_ai_stream',
                   side_effect=lambda env, name, payload: pushed.append(payload)):
            conversation._stream_publisher()('Partial answer')
        self.assertEqual(pushed[0]['channel_id'], channel.id)
        self.assertEqual(pushed[0]['text'], 'Partial answer')
