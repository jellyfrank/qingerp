import json
from contextlib import contextmanager
from unittest.mock import patch

from markupsafe import Markup

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.qing_ai.models.mail_bot import _asks_for_records


def _result(payload):
    return {
        'text': json.dumps(payload), 'model': 'mock-model',
        'usage': {'input_tokens': 20, 'output_tokens': 4, 'total_tokens': 24},
    }


@tagged('post_install', '-at_install')
class TestQingAssistantCompanyData(TransactionCase):
    """Qing Assistant answers record questions through an agent acting as the chatting user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_bool('database.is_neutralized', False)
        cls.manager = new_test_user(
            cls.env, login='assistant-data-manager', lang='en_US',
            groups='base.group_user,base.group_partner_manager', context={'no_reset_password': True},
        )
        cls.reader = new_test_user(
            cls.env, login='assistant-data-reader', lang='en_US', groups='base.group_user',
            context={'no_reset_password': True},
        )
        cls.assistant = cls.env.ref('base.partner_root')
        model = cls.env['ir.model']._get('res.partner.category')
        tag_fields = model.field_id.filtered(lambda field: field.name in ('name', 'color'))
        cls.skill = cls.env['qing.ai.skill'].create({'name': 'Tags'})
        cls.agent = cls.env['qing.ai.agent'].create({
            'name': 'Data Copilot', 'is_default': True, 'sequence': 0,
            'skill_ids': [Command.link(cls.skill.id)],
        })
        tools = {}
        for operation in ('count', 'create'):
            tools[operation] = cls.env['qing.ai.tool'].create({
                'name': 'Tags ' + operation, 'execution_type': 'orm', 'model_id': model.id,
                'orm_operation': operation, 'orm_field_ids': [Command.set(tag_fields.ids)],
                'orm_write_field_ids': [Command.set(tag_fields.ids)] if operation == 'create' else [],
            })
        cls.skill.tool_ids = [Command.set([tool.id for tool in tools.values()])]
        cls.count_tool, cls.create_tool = tools['count'], tools['create']

    @contextmanager
    def _provider(self, plans):
        service = type(self.env['qing.ai.service'])
        with (
            patch.object(service, '_resolve_provider', return_value='fixture'),
            patch.object(service, '_provider_codes', return_value=('fixture',)),
            patch.object(service, '_provider_config', return_value={
                'enabled': True, 'api_key': object(), 'endpoint': 'fixture', 'model': 'mock-model',
            }),
            patch.object(service, '_complete_text', side_effect=plans) as complete,
        ):
            yield complete

    def _chat(self, user):
        return user.with_user(user).with_context(lang='en_US')._init_odoobot()

    def _ask(self, chat, text):
        return chat.message_post(
            body=Markup('<p>%s</p>') % text, message_type='comment', subtype_xmlid='mail.mt_comment',
        )

    def _advance_until_done(self, chat):
        for _step in range(10):
            if not chat.qing_ai_advance()['running']:
                return
        self.fail('The reply must finish within a few steps.')

    def _assistant_replies(self, chat, after):
        return chat.sudo().message_ids.filtered(
            lambda message: message.id > after.id and message.author_id == self.assistant
        ).sorted('id')

    def test_record_question_is_answered_by_the_agent_as_the_chatting_user(self):
        chat = self._chat(self.manager)
        plans = [
            _result({'tool': self.count_tool.key, 'arguments': {}, 'progress': 'Counting the tags.'}),
            _result({'answer': 'You can see some tags.', 'progress': 'Answering.'}),
        ]
        with self._provider(plans) as complete:
            question = self._ask(chat, 'How many tags are there?')
            channel = chat.sudo()
            self.assertEqual(channel.qing_ai_agent_id, self.agent)
            self.assertEqual(channel.qing_ai_owner_id, self.manager)
            self.assertFalse(channel.qing_ai_identity_id, 'Qing Assistant keeps its own identity.')
            conversation = channel.qing_ai_conversation_id
            self.assertEqual(conversation.owner_id, self.manager)
            job = self.env['qing.ai.discuss.job'].search([('message_id', '=', question.id)])
            self.assertFalse(job.general_chat or job.manual_reply)
            self._advance_until_done(chat)
        self.assertEqual(complete.call_count, 2)
        self.assertIn('Qing Assistant (小青)', complete.call_args_list[0].kwargs['system'])
        reply = self._assistant_replies(chat, question)
        self.assertEqual(len(reply), 1)
        self.assertIn('You can see some tags.', reply.body)
        meta = reply._qing_ai_store_meta()
        self.assertEqual(meta['process_steps'][0]['summary'], 'Counting the tags.')
        answers = conversation.sudo().message_ids
        self.assertTrue(answers)
        self.assertEqual(answers.create_uid, self.manager, 'The agent ran as the chatting user.')

    def test_changes_need_the_chatting_users_rights_and_confirmation(self):
        Tag = self.env['res.partner.category']
        plan = {'tool': self.create_tool.key, 'arguments': {'values': {'name': 'Scoped Blue'}},
                'progress': 'Preparing the tag.'}
        reader_chat = self._chat(self.reader)
        with self._provider([_result(plan), _result({'answer': 'I cannot create tags.'})] * 2):
            question = self._ask(reader_chat, 'Create a tag named Scoped Blue')
            self._advance_until_done(reader_chat)
        replies = self._assistant_replies(reader_chat, question)
        self.assertTrue(replies)
        self.assertFalse(any(reply._qing_ai_store_meta()['operations'] for reply in replies))
        self.assertFalse(Tag.search([('name', '=', 'Scoped Blue')]))

        manager_chat = self._chat(self.manager)
        with self._provider([_result(plan)]):
            question = self._ask(manager_chat, 'Create a tag named Scoped Blue')
            self._advance_until_done(manager_chat)
        preview = self._assistant_replies(manager_chat, question)[-1]
        operations = preview._qing_ai_store_meta()['operations']
        self.assertEqual(len(operations), 1)
        self.assertFalse(Tag.search([('name', '=', 'Scoped Blue')]), 'Nothing changes before confirmation.')
        with self.assertRaises(AccessError):
            manager_chat.with_user(self.reader).qing_ai_confirm_operation(operations[0]['id'])
        manager_chat.qing_ai_confirm_operation(operations[0]['id'])
        created = Tag.search([('name', '=', 'Scoped Blue')])
        self.assertEqual(created.create_uid, self.manager)

    def test_greetings_stay_local_and_the_setting_turns_data_access_off(self):
        chat = self._chat(self.manager)
        with self._provider([]) as complete:
            self._ask(chat, '你好')
            complete.assert_not_called()
            self.assertFalse(chat.sudo().qing_ai_agent_id)
            self.env['ir.config_parameter'].sudo().set_bool('qing_ai.assistant_data_access', False)
            question = self._ask(chat, 'How many tags are there?')
        self.assertFalse(chat.sudo().qing_ai_agent_id)
        job = self.env['qing.ai.discuss.job'].search([('message_id', '=', question.id)])
        self.assertTrue(job.general_chat, 'Without data access the old general chat answers.')
        self.assertEqual(chat.qing_ai_advance(), {'running': False, 'progressed': False})

    def test_settings_default_to_data_access(self):
        self.env['ir.config_parameter'].sudo().search([('key', '=', 'qing_ai.assistant_data_access')]).unlink()
        self.assertTrue(self.env['res.config.settings'].create({}).qing_ai_assistant_data_access)
        settings = self.env['res.config.settings'].create({'qing_ai_assistant_data_access': False})
        settings.set_values()
        self.assertFalse(self.env['res.config.settings'].create({}).qing_ai_assistant_data_access)

    def test_record_requests_are_told_apart_from_how_to_questions(self):
        for text in ('查下系统里有多少客户', 'How many open quotations do I have?', '列出最近的销售订单',
                     '帮我新建一个客户叫张三'):
            self.assertTrue(_asks_for_records(text), text)
        for text in ('怎么确认销售订单？', 'How do I create a quotation?', '你好', '写一首诗'):
            self.assertFalse(_asks_for_records(text), text)
