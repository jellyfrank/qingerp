"""Ask requests are answered without the tool catalog unless the model needs ERP tools."""

import json
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged

COMPLETE = 'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text'


def reply(text):
    return {'text': text, 'model': 'mock', 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}


@tagged('post_install', '-at_install')
class TestQingAiPromptRouting(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = 'base.group_user,sales_team.group_sale_salesman'
        if cls.env.ref('qing_partner.group_partner_manager', raise_if_not_found=False):
            groups += ',qing_partner.group_partner_manager'
        cls.user = new_test_user(cls.env, login='qing-ai-routing-user', groups=groups)

    def _conversation(self):
        return self.env['qing.ai.conversation'].with_user(self.user).create({})

    def test_unclear_request_escalates_to_the_planner_with_system_rules_and_json_mode(self):
        conversation = self._conversation()
        plan = json.dumps({'answer': 'Wang Wu has no open quotation.'})
        with patch(COMPLETE, side_effect=[reply('[[ERP]]'), reply(plan)]) as complete:
            result = conversation.assistant_send('帮我看看王五那边的情况')
        self.assertEqual(complete.call_count, 2)
        first, second = complete.call_args_list
        self.assertIn('[[ERP]]', first.kwargs['system'])
        self.assertNotIn('Allowed tools', first.kwargs['system'])
        self.assertFalse(first.kwargs.get('json_mode'))
        self.assertIn('Allowed tools', second.kwargs['system'])
        self.assertTrue(second.kwargs['json_mode'])
        self.assertNotIn('Allowed tools', second.args[0])
        self.assertIn('帮我看看王五那边的情况', second.args[0])
        self.assertEqual(result['messages'][-1]['content'], 'Wang Wu has no open quotation.')

    def test_clear_erp_request_skips_the_answer_first_pass(self):
        conversation = self._conversation()
        plan = json.dumps({'answer': 'There are no quotations.'})
        with patch(COMPLETE, return_value=reply(plan)) as complete:
            conversation.assistant_send('查询本月的报价单')
        complete.assert_called_once()
        self.assertTrue(complete.call_args.kwargs['json_mode'])

    def test_background_ask_answers_in_one_round_without_planning(self):
        conversation = self._conversation()
        opened = conversation.assistant_send('Write a short thank-you note', background=True)
        run = self.env['qing.ai.run'].sudo().browse(opened['active_run']['id'])
        self.assertEqual(run.mode, 'triage')
        with patch(COMPLETE, return_value=reply('Thank you for your support!')) as complete:
            done = conversation.assistant_continue_run(run.id)
        complete.assert_called_once()
        self.assertFalse(done['active_run'])
        self.assertEqual(done['messages'][-1]['content'], 'Thank you for your support!')
        self.assertEqual(done['messages'][-1]['usage']['prompt_tokens'], 10)

    def test_background_ask_switches_to_planning_when_erp_is_needed(self):
        conversation = self._conversation()
        opened = conversation.assistant_send('Continue with what we discussed', background=True)
        run = self.env['qing.ai.run'].sudo().browse(opened['active_run']['id'])
        with patch(COMPLETE, side_effect=[reply('[[ERP]]'), reply(json.dumps({'answer': 'Done.'}))]) as complete:
            step = conversation.assistant_continue_run(run.id)
            self.assertTrue(step['active_run'])
            self.assertEqual(run.mode, 'plan')
            final = conversation.assistant_continue_run(run.id)
        self.assertEqual(complete.call_count, 2)
        self.assertTrue(complete.call_args.kwargs['json_mode'])
        self.assertFalse(final['active_run'])
        self.assertEqual(final['messages'][-1]['content'], 'Done.')

    def test_pending_preview_keeps_follow_ups_on_the_planner(self):
        conversation = self._conversation()
        conversation.sudo().write({'pending_plan': {'remaining': 'Create the quotation.', 'request': 'x'}})
        self.assertTrue(conversation._prefers_business_route('好的'))
        conversation.sudo().write({'pending_plan': False})
        self.assertFalse(conversation._prefers_business_route('好的'))

    def test_field_catalog_is_compact(self):
        conversation = self._conversation()
        catalog = '\n'.join(conversation._business_tool_descriptions().values())
        self.assertIn('runtime readable fields (name|type|label|related model)', catalog)
        self.assertNotIn('"label":', catalog)
