from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiAgents(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='qing-ai-agent-user', groups='base.group_user')
        cls.other = new_test_user(cls.env, login='qing-ai-agent-other', groups='base.group_user')
        cls.skill = cls.env['qing.ai.skill'].create({
            'name': 'Answers', 'instructions': 'Answer with concrete evidence.',
        })
        cls.agent = cls.env['qing.ai.agent'].create({
            'name': 'Operations', 'system_prompt': 'You are an operations specialist.',
            'skill_ids': [Command.link(cls.skill.id)],
        })

    def test_agent_prompt_and_immutable_conversation_agent(self):
        conversation = self.env['qing.ai.conversation'].with_user(self.user).create({
            'agent_id': self.agent.id,
        })
        result = {'text': 'Done', 'model': 'fake', 'usage': {'total_tokens': 4}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result) as complete:
            conversation.with_user(self.user).assistant_send('Check stock')
        # Agent role and skill guidance are trusted instructions, sent apart from user data.
        self.assertIn('operations specialist', complete.call_args.kwargs['system'])
        self.assertIn('Answer with concrete evidence', complete.call_args.kwargs['system'])
        self.assertNotIn('operations specialist', complete.call_args.args[0])
        self.assertEqual(conversation.agent_id, self.agent)
        with self.assertRaises(UserError):
            conversation.with_user(self.user).assistant_select_agent(
                self.env.ref('qing_ai.agent_general').id)

    def test_group_revocation_blocks_new_calls_but_retains_history(self):
        conversation = self.env['qing.ai.conversation'].with_user(self.user).create({
            'agent_id': self.agent.id,
        })
        self.agent.group_ids = [Command.link(self.env.ref('base.group_system').id)]
        with self.assertRaises(AccessError):
            conversation.with_user(self.user).assistant_send('Hello')
        self.assertEqual(conversation.with_user(self.user).assistant_open()['agent_name'], 'Operations')
        with self.assertRaises(AccessError):
            self.env['qing.ai.conversation'].with_user(self.other).assistant_new(self.agent.id)

    def test_regular_user_cannot_change_agent_configuration(self):
        with self.assertRaises(AccessError):
            self.agent.with_user(self.user).write({'system_prompt': 'Bypass rules'})

    def test_agent_in_another_company_is_not_selectable(self):
        company = self.env['res.company'].create({'name': 'Other AI Company'})
        self.agent.company_id = company
        with self.assertRaises(AccessError):
            self.env['qing.ai.conversation'].with_user(self.user).assistant_new(self.agent.id)

    def test_legacy_conversation_binds_available_default_before_call(self):
        conversation = self.env['qing.ai.conversation'].with_user(self.user).create({
            'agent_id': self.agent.id,
        })
        conversation.with_user(self.user).write({'agent_id': False})
        response = {'text': 'Hello', 'model': 'mock', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=response):
            conversation.with_user(self.user).assistant_send('Hello')
        self.assertTrue(conversation.agent_id)
        self.assertTrue(conversation.agent_id.available_for_user())
