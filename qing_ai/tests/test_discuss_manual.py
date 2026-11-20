import json
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiDiscussManual(TransactionCase):

    def test_queued_reply_uses_only_public_text_even_with_professional_enabled(self):
        user = new_test_user(self.env, login='assistant-guided-reader')
        root = self.env.ref('base.partner_root')
        chat = user.with_user(user)._init_odoobot()
        jobs = self.env['qing.ai.discuss.job']
        config = self.env['ir.config_parameter'].sudo()
        config.set_str('qing_ai.professional_guide_consent', config.get_str('database.uuid'))
        config.set_bool('database.is_neutralized', False)
        service_type = type(self.env['qing.ai.service'])
        guide_type = type(self.env['qing.ai.guide'])
        result = {'text': 'Review the access groups. [1]', 'model': 'fixture', 'usage': {}}
        with (
            patch.object(service_type, '_resolve_provider', return_value='fixture'),
            patch.object(service_type, '_provider_codes', return_value=('fixture',)),
            patch.object(service_type, '_provider_config', return_value={
                'enabled': True, 'api_key': object(), 'endpoint': 'fixture', 'model': 'fixture',
            }),
            patch.object(service_type, '_complete_text', return_value=result) as complete,
            patch.object(guide_type, '_request_professional') as professional,
            patch.object(guide_type, '_public_titles') as titles,
        ):
            raw = '新同事登录后看不到对应应用怎么办？ QA-PRIVATE-ORDER-123'
            question = chat.message_post(body=raw, subtype_xmlid='mail.mt_comment', message_type='comment')
            complete.assert_not_called()
            job = jobs.search([('message_id', '=', question.id)])
            self.assertEqual(job.state, 'pending')
            job._process_one()
            self.assertEqual(job.state, 'done')
            complete.assert_called_once()
            payload = json.loads(complete.call_args.args[0])
            self.assertIn('看不到对应应用', payload['question'])
            self.assertNotIn('QA-PRIVATE-ORDER-123', json.dumps(payload))
            self.assertEqual(len(payload['passages']), 1)
            professional.assert_not_called()
            titles.assert_not_called()
        reply = chat.message_ids.filtered(lambda message: message.parent_id == question and message.author_id == root)
        self.assertEqual(len(reply), 1)
        self.assertIn('Review the access groups. [1]', reply.body)
        self.assertIn('https://doc.qingerp.com/', reply.body)
