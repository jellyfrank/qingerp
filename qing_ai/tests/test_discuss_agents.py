from unittest.mock import patch

from markupsafe import Markup

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiDiscussAgents(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='qing-ai-discuss-user', groups='base.group_user')
        cls.agent = cls.env['qing.ai.agent'].create({
            'name': 'Sales Copilot',
            'description': 'Answers sales questions in Discuss.',
        })

    def _open_channel(self, company=None):
        Agent = self.agent.with_user(self.user)
        if company:
            Agent = Agent.with_company(company)
        action = Agent.action_open_discuss()
        channel_id = int(action['params']['active_id'].rsplit('_', 1)[-1])
        return self.env['discuss.channel'].sudo().browse(channel_id), action

    def test_open_agent_creates_private_native_chat_and_reuses_it(self):
        channel, action = self._open_channel()
        self.assertEqual(action['tag'], 'mail.action_discuss')
        self.assertEqual(channel.channel_type, 'chat')
        self.assertEqual(channel.qing_ai_agent_id, self.agent)
        self.assertEqual(channel.qing_ai_owner_id, self.user)
        self.assertEqual(channel.qing_ai_conversation_id.owner_id, self.user)
        self.assertEqual(channel.qing_ai_identity_id.agent_id, self.agent)
        self.assertEqual(channel.qing_ai_identity_id.company_id, self.user.company_id)
        self.assertEqual(
            channel.channel_member_ids.partner_id,
            self.user.partner_id | channel.qing_ai_identity_id.partner_id,
        )
        same_channel, _action = self._open_channel()
        self.assertEqual(same_channel, channel)
        self.assertEqual(same_channel.qing_ai_conversation_id, channel.qing_ai_conversation_id)

    def test_discuss_message_uses_agent_conversation_and_posts_agent_reply(self):
        channel, _action = self._open_channel()
        response = {
            'text': '**Qualified answer**',
            'model': 'mock-model',
            'usage': {'total_tokens': 9},
        }
        with patch(
            'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
            return_value=response,
        ) as complete:
            channel.with_user(self.user).message_post(
                body=Markup('<p>Hello <b>agent</b></p>'),
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
            # Posting only queues the reply; the provider is called by the reply cron.
            complete.assert_not_called()
            job = self.env['qing.ai.discuss.job'].search([('channel_id', '=', channel.id)])
            self.assertEqual(job.state, 'pending')
            self.assertEqual(self.env['qing.ai.discuss.job']._process_pending(), 1)
        complete.assert_called_once()
        self.assertEqual(job.state, 'done')
        self.assertIn('Hello', complete.call_args.args[0])
        self.assertIn('agent', complete.call_args.args[0])
        conversation = channel.qing_ai_conversation_id
        self.assertEqual(conversation.message_ids.mapped('role'), ['user', 'assistant'])
        replies = self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', channel.id),
            ('author_id', '=', channel.qing_ai_identity_id.partner_id.id),
        ])
        self.assertEqual(len(replies), 1)
        self.assertIn('<strong>Qualified answer</strong>', replies.body)

    def test_agent_avatar_is_synced_to_company_discuss_identity(self):
        self.agent.image_1920 = self.env['res.partner']._avatar_get_placeholder()
        channel, _action = self._open_channel()
        identity = channel.qing_ai_identity_id
        self.assertTrue(identity.partner_id.image_1920)
        self.agent.image_1920 = False
        self.assertFalse(identity.partner_id.with_context(active_test=False).image_1920)
        self.agent.name = 'Renamed Copilot'
        self.assertEqual(identity.partner_id.with_context(active_test=False).name, 'Renamed Copilot')

    def test_global_agent_uses_separate_chat_identity_per_company(self):
        company = self.env['res.company'].create({'name': 'Second AI Company'})
        self.user.sudo().write({'company_ids': [Command.link(company.id)]})
        first_channel, _action = self._open_channel()
        second_channel, _action = self._open_channel(company)
        self.assertNotEqual(first_channel, second_channel)
        self.assertNotEqual(first_channel.qing_ai_identity_id, second_channel.qing_ai_identity_id)
        self.assertEqual(second_channel.qing_ai_conversation_id.company_id, company)

    def test_agent_replies_in_a_chat_opened_from_a_secondary_company(self):
        company = self.env['res.company'].create({'name': 'Secondary Reply Company'})
        self.user.sudo().write({'company_ids': [Command.link(company.id)]})
        channel, _action = self._open_channel(company)
        self.assertNotEqual(self.user.company_id, company)
        response = {'text': 'Secondary company answer', 'model': 'mock-model', 'usage': {}}
        with patch(
            'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
            return_value=response,
        ) as complete:
            channel.with_user(self.user).message_post(
                body=Markup('<p>Hello from the second company</p>'),
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
            self.env['qing.ai.discuss.job']._process_pending()
        complete.assert_called_once()
        replies = self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', channel.id),
            ('author_id', '=', channel.qing_ai_identity_id.partner_id.id),
        ])
        self.assertEqual(len(replies), 1)
        self.assertIn('Secondary company answer', replies.body)

    def test_failed_reply_marks_the_job_and_old_jobs_are_vacuumed(self):
        channel, _action = self._open_channel()
        with patch(
            'odoo.addons.qing_ai.models.discuss_channel.DiscussChannel._qing_ai_reply_to_message',
            side_effect=RuntimeError('provider crashed'),
        ):
            channel.with_user(self.user).message_post(
                body=Markup('<p>Will fail</p>'), message_type='comment', subtype_xmlid='mail.mt_comment',
            )
            Job = self.env['qing.ai.discuss.job']
            Job._process_pending()
        job = Job.search([('channel_id', '=', channel.id)])
        self.assertEqual(job.state, 'failed')
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE qing_ai_discuss_job SET write_date = now() - interval '8 days' WHERE id = %s", [job.id],
        )
        job.invalidate_recordset()
        self.assertEqual(Job._gc_finished_jobs(), (1, False))
        self.assertFalse(job.exists())
