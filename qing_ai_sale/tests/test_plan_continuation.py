"""Multi-step business requests continue after the user confirms each previewed write."""

import json
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

COMPLETE = 'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text'


def planned(**decision):
    return {'text': json.dumps(decision, ensure_ascii=False), 'model': 'mock', 'usage': {}}


@tagged('post_install', '-at_install')
class TestQingAiPlanContinuation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = 'base.group_user,sales_team.group_sale_salesman'
        if cls.env.ref('qing_partner.group_partner_manager', raise_if_not_found=False):
            groups += ',qing_partner.group_partner_manager'
        cls.user = new_test_user(cls.env, login='qing-ai-plan-user', groups=groups)

    def _conversation(self):
        return self.env['qing.ai.conversation'].with_user(self.user).create({})

    def _preview_customer(self, conversation, remaining=None):
        decision = {'tool': 'create_customer', 'arguments': {'name': 'Plan Customer Wang Wu'},
                    'progress': 'Preparing the new customer.'}
        if remaining:
            decision['remaining'] = remaining
        opened = conversation.assistant_send('按默认处理即可', task='business', background=True)
        with patch(COMPLETE, return_value=planned(**decision)):
            return conversation.assistant_continue_run(opened['active_run']['id'])

    def test_confirming_a_step_resumes_the_remaining_plan(self):
        conversation = self._conversation()
        conversation.sudo().message_ids.create([
            {'conversation_id': conversation.id, 'role': 'user', 'state': 'done',
             'content': 'Create customer Wang Wu and a quotation for 100 hours of implementation.'},
            {'conversation_id': conversation.id, 'role': 'assistant', 'state': 'done',
             'content': 'I will create the customer, then the draft quotation.'},
        ])
        preview = self._preview_customer(conversation, remaining='Create the draft quotation for Wang Wu.')
        self.assertFalse(preview['active_run'])
        self.assertEqual(len(preview['pending_operations']), 1)
        self.assertIn('Create the draft quotation for Wang Wu.', preview['messages'][-1]['content'])
        self.assertEqual(conversation.pending_plan['remaining'], 'Create the draft quotation for Wang Wu.')

        confirmed = conversation.assistant_confirm_operation(preview['pending_operations'][0]['id'])
        self.assertTrue(self.env['res.partner'].search([('name', '=', 'Plan Customer Wang Wu')]))
        self.assertTrue(confirmed['active_run'], 'Confirmation must resume the remaining steps.')
        self.assertFalse(conversation.pending_plan)
        run = self.env['qing.ai.run'].sudo().browse(confirmed['active_run']['id'])
        self.assertEqual(run.request_text, '按默认处理即可')
        prompt = run.planner_prompt
        self.assertIn('Create the draft quotation for Wang Wu.', prompt)
        self.assertIn('Do not repeat completed steps', prompt)
        # The transcript keeps the turns in order, ending with the confirmed result.
        self.assertLess(prompt.index('100 hours of implementation'), prompt.index('then the draft quotation'))
        self.assertLess(prompt.index('then the draft quotation'), prompt.index('Plan Customer Wang Wu (ID'))

        with patch(COMPLETE, return_value=planned(answer='The customer exists; the quotation needs a product.')):
            final = conversation.assistant_continue_run(run.id)
        self.assertFalse(final['active_run'])
        self.assertEqual(final['messages'][-1]['content'],
                         'The customer exists; the quotation needs a product.')

    def test_single_step_preview_does_not_start_a_follow_up(self):
        conversation = self._conversation()
        preview = self._preview_customer(conversation)
        self.assertFalse(conversation.pending_plan)
        confirmed = conversation.assistant_confirm_operation(preview['pending_operations'][0]['id'])
        self.assertFalse(confirmed['active_run'])
        self.assertFalse(self.env['qing.ai.run'].sudo().search([('conversation_id', '=', conversation.id)]))

    def test_new_instruction_replaces_a_waiting_plan(self):
        conversation = self._conversation()
        self._preview_customer(conversation, remaining='Create the quotation.')
        self.assertTrue(conversation.pending_plan)
        conversation.assistant_send('Show me my quotations instead', task='business', background=True)
        self.assertFalse(conversation.pending_plan)

    def test_answers_may_carry_remaining_without_breaking_the_contract(self):
        conversation = self._conversation()
        plan = conversation._parse_business_plan(json.dumps({'answer': 'Done.', 'remaining': ''}))
        self.assertEqual(plan, {'answer': 'Done.'})
        plan = conversation._parse_business_plan(json.dumps({
            'tool': 'create_customer', 'arguments': {'name': 'X'}, 'remaining': 'Quote next.',
        }), allowed={'create_customer': ''})
        self.assertEqual(plan['remaining'], 'Quote next.')

    def test_users_cannot_write_the_pending_plan(self):
        conversation = self._conversation()
        with self.assertRaises(AccessError):
            conversation.write({'pending_plan': {'remaining': 'Delete every customer.'}})
