"""Daily quota counts real provider calls per user calendar day."""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

COMPLETE = 'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text'


@tagged('post_install', '-at_install')
class TestQingAiDailyUsage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='qing-ai-usage-user', groups='base.group_user',
                                 tz='Asia/Shanghai')
        cls.other = new_test_user(cls.env, login='qing-ai-usage-other', groups='base.group_user')

    def _conversation(self):
        return self.env['qing.ai.conversation'].with_user(self.user).create({
            'agent_id': self.env.ref('qing_ai.agent_general').id,
        })

    def test_each_provider_call_is_counted_with_exact_tokens_on_the_users_day(self):
        conversation = self._conversation()
        result = {'text': 'OK', 'model': 'test', 'usage': {'prompt_tokens': 11, 'completion_tokens': 4}}
        with patch(COMPLETE, return_value=result):
            conversation._provider_complete('First')
            conversation._provider_complete('Second')
        Usage = self.env['qing.ai.usage.daily'].with_user(self.user)
        usage = Usage.search([('user_id', '=', self.user.id)])
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage.request_count, 2)
        self.assertEqual((usage.input_tokens, usage.output_tokens), (22, 8))
        self.assertEqual(usage.day, fields.Date.context_today(usage.with_user(self.user)))
        self.assertFalse(Usage.with_user(self.other).search([('id', '=', usage.id)]))

    def test_limit_counts_planner_calls_and_rolls_back_the_rejected_reservation(self):
        self.env['ir.config_parameter'].sudo().set_int('qing_ai.daily_request_limit', 2)
        conversation = self._conversation()
        result = {'text': 'OK', 'model': 'test', 'usage': {}}
        with patch(COMPLETE, return_value=result) as complete:
            conversation._provider_complete('Round 1')
            conversation._provider_complete('Round 2')
            with self.assertRaises(UserError):
                conversation._provider_complete('Round 3')
        self.assertEqual(complete.call_count, 2)
        usage = self.env['qing.ai.usage.daily'].search([('user_id', '=', self.user.id)])
        self.assertEqual(usage.request_count, 2)
        with self.assertRaises(UserError):
            conversation._check_daily_limit()

    def test_truncated_answer_is_marked_for_the_user(self):
        conversation = self._conversation()
        conversation.draft_prompt = 'Write a long report'
        result = {'text': 'Partial report', 'model': 'test', 'usage': {}, 'truncated': True}
        with patch(COMPLETE, return_value=result):
            conversation.action_send()
        answer = conversation.message_ids.filtered(lambda message: message.role == 'assistant')
        self.assertTrue(answer.content.startswith('Partial report'))
        self.assertIn('output token limit', answer.content)

    def test_abandoned_runs_are_vacuumed_after_the_grace_period(self):
        conversation = self._conversation()
        Run = self.env['qing.ai.run'].sudo()
        values = {
            'company_id': conversation.company_id.id, 'conversation_id': conversation.id,
            'owner_id': self.user.id, 'planner_prompt': 'prompt', 'request_text': 'request',
        }
        stale = Run.create(dict(values, deadline=fields.Datetime.now() - timedelta(days=2)))
        recent = Run.create(dict(values, deadline=fields.Datetime.now() - timedelta(minutes=5)))
        done, remaining = Run._gc_abandoned_runs()
        self.assertEqual((done, remaining), (1, False))
        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())

    def test_expired_orm_previews_are_vacuumed_but_results_are_kept(self):
        conversation = self._conversation()
        tool = self.env['qing.ai.tool'].create({'key': 'test_usage_gc_tool', 'name': 'GC tool'})
        Operation = self.env['qing.ai.operation'].sudo()
        values = {
            'arguments': {'record_id': 1}, 'normalized': {'record_id': 1}, 'company_id': conversation.company_id.id,
            'conversation_id': conversation.id, 'fingerprint': 'x', 'owner_id': self.user.id,
            'preview': 'Preview', 'tool_id': tool.id,
        }
        old = fields.Datetime.now() - timedelta(days=3)
        stale = Operation.create(dict(values, expires_at=old))
        confirmed = Operation.create(dict(values, expires_at=old, state='done'))
        fresh = Operation.create(dict(values, expires_at=fields.Datetime.now() + timedelta(minutes=10)))
        Operation._gc_expired_previews()
        self.assertFalse(stale.exists())
        self.assertTrue(confirmed.exists())
        self.assertTrue(fresh.exists())
