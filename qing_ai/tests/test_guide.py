import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestQingAiGuide(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(cls.env, login='manual-reader', groups='base.group_user')
        cls.guide = cls.env['qing.ai.guide'].with_user(cls.reader)
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_str('qing_ai.professional_guide_consent', '')
        cls.params.set_bool('database.is_neutralized', False)
        cls.model_result = {'text': 'Review user groups. [1]', 'model': 'test', 'usage': {}}

    def test_public_question_uses_only_canonical_topic_and_public_passage(self):
        Guide = type(self.guide)
        Conversation = type(self.env['qing.ai.conversation'])
        with (
            patch.object(Conversation, '_provider_complete', return_value=self.model_result) as model,
            patch.object(Guide, '_request_professional') as hosted,
            patch.object(type(self.env['qing.service.instance']), '_request_json') as titles,
        ):
            answer = self.guide.guide_answer('user_access')
            payload = json.loads(model.call_args.args[0])
            self.assertEqual(set(payload), {'topic', 'language', 'version', 'passages'})
            self.assertEqual(payload['version'], '20.0')
            self.assertEqual(len(payload['passages']), 1)
            self.assertTrue(model.call_args.kwargs['guide'])
            self.assertEqual(answer['topic'], 'user_access')
            self.assertIn('https://doc.qingerp.com/', answer['references'][0]['url'])
            with self.assertRaises(UserError):
                self.guide.guide_answer('客户甲 公司 8888 元 SO001 user_access')
            self.assertEqual(model.call_count, 1)
            hosted.assert_not_called()
            titles.assert_not_called()

    def test_preset_question_is_selected_on_server(self):
        Conversation = type(self.env['qing.ai.conversation'])
        with patch.object(Conversation, '_provider_complete', return_value=self.model_result) as model:
            self.guide.guide_answer('user_access', question_id='2642-079')
            payload = json.loads(model.call_args.args[0])
            self.assertIn('看不到对应应用', payload['question'])
            with self.assertRaises(UserError):
                self.guide.guide_answer('user_access', question_id='客户甲 SO001 8888 元')
            with self.assertRaises(UserError):
                self.guide.guide_answer('user_access', question_id='2642-001')
            self.assertEqual(model.call_count, 1)

    def test_title_consent_once_per_local_day_and_topic(self):
        Instance = type(self.env['qing.service.instance'])
        Conversation = type(self.env['qing.ai.conversation'])
        with (
            patch.object(Conversation, '_provider_complete', return_value=self.model_result),
            patch.object(Instance, '_request_json', return_value={'items': [{'title': 'Public title'}]}) as titles,
        ):
            self.assertTrue(self.guide.set_guide_title_consent(True))
            self.assertEqual(self.guide.guide_answer('user_access')['titles'], ['Public title'])
            self.assertEqual(titles.call_args.args[2], {
                'topic': 'user_access', 'version': '20.0', 'modules': ['qing_base'],
            })
            self.assertFalse(self.guide.guide_answer('user_access')['titles'])
            self.assertFalse(self.guide.set_guide_title_consent(False))
            self.assertFalse(self.guide.guide_answer('user_access')['titles'])
            titles.assert_called_once()

    def test_professional_answer_and_citation_have_fresh_entitlement_requests(self):
        self.params.set_str('qing_ai.professional_guide_consent', self.guide._database_uuid())
        section = {
            'uid': 'a' * 32, 'title': 'Implementation checklist', 'chapter': 'Permissions',
            'revision': 1, 'module': 'qing_base', 'version': '20.0', 'body': 'Approved passage',
        }
        Conversation = type(self.env['qing.ai.conversation'])
        with (
            patch.object(Conversation, '_provider_complete', return_value={
                **self.model_result, 'text': 'Public topic. [1] Approved passage. [2]',
            }) as model,
            patch.object(type(self.guide), '_request_professional', return_value={'items': [section]}) as remote,
        ):
            result = self.guide.guide_answer('user_access')
            self.assertEqual(result['references'][1]['uid'], section['uid'])
            self.assertNotIn(section['body'], json.dumps(result['references']))
            self.assertIn(section['body'], model.call_args.args[0])
            self.assertEqual(self.guide.guide_citation('user_access', section['uid']), section)
            self.assertEqual([call.args[0] for call in remote.call_args_list], ['search', 'source'])

            remote.side_effect = UserError('Guide unavailable')
            result = self.guide.guide_answer('user_access')
            self.assertTrue(result['notice'])
            self.assertEqual(len(result['references']), 1)
            self.assertNotIn(section['body'], model.call_args.args[0])
            with self.assertRaises(UserError):
                self.guide.guide_citation('user_access', section['uid'])

    def test_unsupported_citation_is_never_returned_as_the_final_answer(self):
        Conversation = type(self.env['qing.ai.conversation'])
        invalid = dict(self.model_result, text='Invented step. [9]')
        with patch.object(Conversation, '_provider_complete', return_value=invalid):
            result = self.guide.guide_answer('user_access')
            self.assertNotIn('Invented step', result['answer'])
            self.assertEqual(len(result['references']), 1)

    def test_stream_previews_only_complete_lines_with_available_citations(self):
        with patch('odoo.addons.qing_ai.models.qing_ai_guide._push_ai_stream') as publish:
            on_delta = self.guide._stream_answer('a' * 32, {'1'})
            on_delta('Cited text. [')
            on_delta('1]')
            publish.assert_not_called()
            on_delta('\n')
            self.assertEqual(publish.call_args.args[2]['text'], 'Cited text. [1]\n')
            on_delta('Unavailable reference. [2]\n')
            on_delta('More text. [1]\n')
            publish.assert_called_once()

    def test_final_guard_rejects_uncited_lines_alongside_a_valid_reference(self):
        Conversation = type(self.env['qing.ai.conversation'])
        invalid = dict(self.model_result, text='Invented step.\nReview user groups. [1]')
        with patch.object(Conversation, '_provider_complete', return_value=invalid):
            result = self.guide.guide_answer('user_access')
            self.assertNotIn('Invented step', result['answer'])
            self.assertEqual(len(result['references']), 1)

    def test_citation_format_retries_once_without_streaming_the_unverified_output(self):
        Conversation = type(self.env['qing.ai.conversation'])
        wrong = dict(self.model_result, text='Review user groups. [1]\nThe passage omits the access policy.')
        revised = dict(self.model_result, text='Review user groups. [1]\nThe passage omits the access policy. [1]')
        with patch.object(Conversation, '_provider_complete', side_effect=[wrong, revised]) as model:
            result = self.guide.guide_answer('user_access', request_key='a' * 32)
        self.assertEqual(result['answer'], revised['text'])
        first, retry = model.call_args_list
        self.assertEqual(first.args[0], retry.args[0])
        self.assertIn('previous response could not be displayed', retry.kwargs['system'])
        self.assertNotIn('on_delta', retry.kwargs)

    def test_portal_cannot_use_any_guide_rpcs(self):
        portal = new_test_user(self.env, login='manual-portal', groups='base.group_portal')
        with self.assertRaises(AccessError):
            self.guide.with_user(portal).guide_configuration()
        with self.assertRaises(AccessError):
            self.guide.with_user(portal).guide_answer('user_access')
        with self.assertRaises(AccessError):
            self.guide.with_user(portal).set_guide_title_consent(True)
