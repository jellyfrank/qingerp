from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGlm(TransactionCase):

    def test_vision_model_uses_image_content_blocks(self):
        service = self.env['qing.ai.service']
        config = {
            'api_key': 'test-key', 'enabled': True, 'image_enabled': True,
            'endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
            'model': 'glm-4v',
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'choices': [{'message': {'content': 'OK'}}],
            }
            service._complete_text('Describe', provider='glm', config=config,
                                   image={'mimetype': 'image/png', 'data': 'YWJj'})
        content = post.call_args.kwargs['json']['messages'][0]['content']
        self.assertEqual(content[1]['image_url']['url'], 'data:image/png;base64,YWJj')

    def test_saved_settings_and_chat_request(self):
        settings = self.env['res.config.settings'].create({
            'qing_glm_api_key': 'test-key',
            'qing_glm_enabled': True,
            'qing_glm_endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
            'qing_glm_model': 'glm-4-flash',
        })
        settings.execute()
        service = self.env['qing.ai.service']
        with patch('odoo.addons.qing_ai.models.qing_ai_service.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                'model': 'glm-4-flash',
                'choices': [{'message': {'content': 'OK'}}],
            }
            result = service._complete_text('Hi', provider='glm')
            self.assertEqual(result['text'], 'OK')
            self.assertIn('/chat/completions', post.call_args.args[0])
            self.assertEqual(post.call_args.kwargs['json']['model'], 'glm-4-flash')
            self.assertEqual(post.call_args.kwargs['json']['max_tokens'], 4096)
            self.assertEqual(settings.action_test_qing_glm()['tag'], 'display_notification')
