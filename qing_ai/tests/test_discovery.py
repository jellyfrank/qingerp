"""Discovery records are inert until a reviewed runtime adapter exists."""

from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiDiscovery(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='qing-ai-discovery-user', groups='base.group_user')

    def test_scan_is_admin_only_and_idempotent(self):
        module = self.env['ir.module.module'].search([('name', '=', 'qing_ai')], limit=1)
        Tool = self.env['qing.ai.tool']
        with self.assertRaises(AccessError):
            Tool.with_user(self.user).discover_module(module.id)
        first = Tool.discover_module(module.id)
        self.assertTrue(first)
        self.assertTrue(all(tool.review_state == 'needs_adapter' for tool in first))
        second = Tool.discover_module(module.id)
        self.assertEqual(set(first.ids), set(second.ids))
        self.assertFalse(first.available_for_user())
        with self.assertRaises(UserError):
            first[:1].write({'review_state': 'published'})

    def test_method_analysis_sends_literal_free_source_only_on_admin_request(self):
        module = self.env['ir.module.module'].search([('name', '=', 'qing_ai')], limit=1)
        buttons = self.env['qing.ai.tool'].discover_module(module.id).filtered('method_name')
        self.assertTrue(buttons)
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value={'text': 'Review side effects.', 'model': 'test', 'usage': {}}) as complete:
            buttons[:1].action_analyze_source()
        self.assertEqual(buttons[:1].ai_analysis, 'Review side effects.')
        self.assertIn('Literal values have been removed', complete.call_args.args[0])
        with self.assertRaises(AccessError):
            buttons[:1].with_user(self.user).action_analyze_source()

    def test_candidate_cannot_be_added_as_executable_skill(self):
        Tool = self.env['qing.ai.tool']
        candidate = Tool.create({
            'key': 'discovered:test-candidate', 'name': 'Unknown operation',
            'review_state': 'needs_adapter',
        })
        skill = self.env['qing.ai.skill'].create({
            'name': 'Unreviewed operation', 'tool_ids': [Command.link(candidate.id)],
        })
        agent = self.env['qing.ai.agent'].create({
            'name': 'Review Agent', 'skill_ids': [Command.link(skill.id)],
        })
        conversation = self.env['qing.ai.conversation'].with_user(self.user).create({
            'agent_id': agent.id,
        })
        self.assertEqual(skill.skill_type, 'guidance')
        self.assertFalse(conversation._agent_tool_keys())
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value={'text': 'No operation performed.', 'model': 'test', 'usage': {}}):
            reply = conversation.assistant_send('Can you run it?')
        self.assertIn('No operation performed', reply['messages'][-1]['content'])
