from contextlib import contextmanager
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAssistantChat(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        reader = new_test_user(cls.env, login='assistant-chat-reader', lang='en_US',
                               context={'no_reset_password': True})
        cls.chat = reader.with_user(reader).with_context(lang='en_US')._init_odoobot()
        cls.assistant = cls.env.ref('base.partner_root')
        cls.jobs = cls.env['qing.ai.discuss.job']
        cls.env['ir.config_parameter'].sudo().set_bool('database.is_neutralized', False)
        # These cases cover the general chat used when Qing Assistant may not query company data.
        cls.env['ir.config_parameter'].sudo().set_bool('qing_ai.assistant_data_access', False)

    @contextmanager
    def _configured_provider(self):
        service = type(self.env['qing.ai.service'])
        result = {'text': 'Here is a birthday greeting!', 'model': 'fixture-model', 'usage': {}}
        with (
            patch.object(service, '_resolve_provider', return_value='fixture'),
            patch.object(service, '_provider_codes', return_value=('fixture',)),
            patch.object(service, '_provider_config', return_value={
                'enabled': True, 'api_key': object(), 'endpoint': 'fixture', 'model': 'fixture-model',
            }),
            patch.object(service, '_complete_text', return_value=result) as complete,
        ):
            yield complete

    def _send(self, text):
        return self.chat.message_post(body=text, subtype_xmlid='mail.mt_comment', message_type='comment')

    def _replies(self, question):
        return self.chat.message_ids.filtered(lambda message: message.id > question.id and message.author_id == self.assistant)

    def test_model_question_and_greeting_reply_locally(self):
        with self._configured_provider() as complete:
            for question in ('你是哪个模型？', 'What model are you using?'):
                message = self._send(question)
                self.assertIn('fixture-model', self._replies(message).body)
                self.assertFalse(self.jobs.search_count([('message_id', '=', message.id)]))
            message = self._send('小青，你好！')
            self.assertIn('What would you like to talk about?', self._replies(message).body)
            complete.assert_not_called()

    def test_normal_chat_sends_only_the_current_text_and_no_tools(self):
        with self._configured_provider() as complete:
            previous = self._send('QingERP 自定义工作流 SO0042 INTERNAL-ONLY')
            self.assertFalse(self.jobs.search_count([('message_id', '=', previous.id)]))
            message = self._send('Please write a short birthday greeting.')
            job = self.jobs.search([('message_id', '=', message.id)])
            self.assertTrue(job.general_chat)
            complete.assert_not_called()
            job._process_one()
            self.assertEqual(job.state, 'done')
            complete.assert_called_once()
            self.assertEqual(complete.call_args.args[0], 'Please write a short birthday greeting.')
            self.assertNotIn('tools', complete.call_args.kwargs)
        reply = self._replies(message)
        self.assertEqual(reply.parent_id, message)
        self.assertIn('Here is a birthday greeting!', reply.body)
        self.assertNotIn('https://doc.qingerp.com/', reply.body)

    def test_general_job_rechecks_a_message_edited_to_contain_erp_details(self):
        with self._configured_provider() as complete:
            message = self._send('Please write a brief party invitation.')
            job = self.jobs.search([('message_id', '=', message.id)])
            self.assertTrue(job.general_chat)
            message.sudo().write({'body': 'QingERP 自定义工作流 SO0042 INTERNAL-ONLY'})
            job._process_one()
            self.assertEqual(job.state, 'done')
            complete.assert_not_called()
        self.assertTrue(self._replies(message))
