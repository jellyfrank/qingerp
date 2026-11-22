import json
from unittest.mock import patch

from markupsafe import Markup

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

COMPLETE = 'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text'
ROUNDS = 'odoo.addons.qing_ai.models.qing_ai_conversation_tools.MAX_PLANNER_ROUNDS'


def _result(payload):
    return {'text': json.dumps(payload), 'model': 'mock-model',
            'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}}


@tagged('post_install', '-at_install')
class TestQingAiReadableAnswers(TransactionCase):
    """A planner run that stops at its limits still answers in readable text, never raw JSON."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login='qing-ai-readable', lang='en_US',
            groups='base.group_user,base.group_partner_manager',
        )
        model = cls.env['ir.model']._get('res.partner.category')
        tag_fields = model.field_id.filtered(lambda field: field.name in ('name', 'color'))
        cls.skill = cls.env['qing.ai.skill'].create({'name': 'Read Tags'})
        cls.agent = cls.env['qing.ai.agent'].create({
            'name': 'Readable Copilot', 'skill_ids': [Command.link(cls.skill.id)],
        })
        tools = {}
        for operation in ('search', 'count'):
            tools[operation] = cls.env['qing.ai.tool'].create({
                'name': 'Tags ' + operation, 'execution_type': 'orm', 'model_id': model.id,
                'orm_operation': operation, 'orm_field_ids': [Command.set(tag_fields.ids)],
            })
        cls.skill.tool_ids = [Command.set([tool.id for tool in tools.values()])]
        cls.search_tool, cls.count_tool = tools['search'], tools['count']
        cls.env['res.partner.category'].create([{'name': 'Readable Blue'}, {'name': 'Readable Red'}])
        cls.name_label = cls.env['res.partner.category']._fields['name'].string

    def _ask_until_done(self, text, replies):
        result = self.env['qing.ai.agent'].with_user(self.user).assistant_launch_chat(self.agent.id)
        channel = self.env['discuss.channel'].browse(result['channel_id']).with_user(self.user)
        channel.message_post(body=Markup('<p>%s</p>') % text, message_type='comment',
                             subtype_xmlid='mail.mt_comment')
        plans = [
            _result({'tool': self.search_tool.key, 'arguments': {}, 'progress': 'Listing the tags.'}),
            _result({'tool': self.count_tool.key, 'arguments': {}, 'progress': 'Counting the tags.'}),
        ] + replies
        with patch(ROUNDS, 2), patch(COMPLETE, side_effect=plans) as complete:
            for _step in range(10):
                if not channel.qing_ai_advance()['running']:
                    break
        identity = channel.sudo().qing_ai_identity_id.partner_id
        reply = channel.sudo().message_ids.filtered(lambda message: message.author_id == identity)[:1]
        return reply, complete

    def test_planning_limit_answers_from_the_results_in_plain_text(self):
        summary = {'text': 'There are several tags, including Readable Blue and Readable Red.',
                   'model': 'mock-model', 'usage': {'input_tokens': 30, 'output_tokens': 9, 'total_tokens': 39}}
        reply, complete = self._ask_until_done('How many tags are there and what are they called?', [summary])
        self.assertEqual(complete.call_count, 3, 'Two planner rounds, then one answer from the results.')
        final = complete.call_args_list[-1]
        self.assertNotIn('tools', final.kwargs)
        self.assertIn('Never output JSON', final.kwargs['system'])
        self.assertIn('Readable Blue', final.args[0], 'The answer is written from the verified results.')
        self.assertIn('Readable Blue and Readable Red', reply.body)
        self.assertNotIn('```', reply.body)
        self.assertNotIn('Query result', reply.body)
        self.assertEqual(reply._qing_ai_store_meta()['usage_summary']['input_tokens'], 50)

    def test_without_the_final_answer_the_result_is_a_readable_table(self):
        reply, _complete = self._ask_until_done('How many tags are there and what are they called?',
                                                [UserError('The provider is unavailable.')])
        self.assertIn('Matching records:', reply.body)
        self.assertIn(self.name_label, reply.body, 'Columns use field labels, not technical names.')
        self.assertIn('Readable Blue', reply.body)
        self.assertNotIn('{&quot;count&quot;', reply.body)
        self.assertNotIn('"count"', reply.body)
