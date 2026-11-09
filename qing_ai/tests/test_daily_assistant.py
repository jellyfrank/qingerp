import base64
import io
from unittest.mock import patch

from PIL import Image
from reportlab.pdfgen import canvas

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiDailyAssistant(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='qing-ai-assistant-user', groups='base.group_user')
        cls.other_user = new_test_user(cls.env, login='qing-ai-assistant-other', groups='base.group_user')

    def _conversation(self, user=None):
        return self.env['qing.ai.conversation'].with_user(user or self.user).create({
            'agent_id': self.env.ref('qing_ai.agent_general').id,
        })

    def test_owner_only_history_and_read_only_messages(self):
        conversation = self._conversation()
        Conversation = self.env['qing.ai.conversation'].with_user(self.other_user)
        self.assertFalse(Conversation.search([('id', '=', conversation.id)]))
        with self.assertRaises(AccessError):
            Conversation.browse(conversation.id).read(['name'])
        with self.assertRaises(AccessError):
            Conversation.browse(conversation.id).action_send()
        with self.assertRaises(AccessError):
            conversation.with_user(self.user).write({'owner_id': self.other_user.id})
        with self.assertRaises(AccessError):
            self.env['qing.ai.message'].with_user(self.user).create({
                'conversation_id': conversation.id,
                'content': 'Forged reply',
                'role': 'assistant',
                'state': 'done',
            })

    def test_owner_can_delete_conversation_with_private_attachments(self):
        conversation = self._conversation()
        message = self.env['qing.ai.message'].sudo().create({
            'conversation_id': conversation.id,
            'content': 'My attachment',
            'role': 'user',
            'state': 'done',
        })
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'private.txt',
            'type': 'binary',
            'datas': base64.b64encode(b'private contents'),
            'res_model': 'qing.ai.message',
            'res_id': message.id,
            'public': False,
        })
        message.sudo().write({'file_attachment_ids': [(4, attachment.id)]})
        with self.assertRaises(AccessError):
            conversation.with_user(self.other_user).assistant_delete()
        self.assertTrue(conversation.exists())
        self.assertTrue(conversation.with_user(self.user).assistant_delete())
        self.assertFalse(conversation.exists())
        self.assertFalse(message.exists())
        self.assertFalse(attachment.exists())

    def test_message_history_and_usage_are_saved(self):
        conversation = self._conversation()
        conversation.with_user(self.user).write({'draft_prompt': 'Hello'})
        result = {
            'text': 'First answer',
            'model': 'test-model',
            'usage': {
                'input_tokens': 5,
                'output_tokens': 3,
                'total_tokens': 8,
                'output_tokens_details': {'reasoning_tokens': 2},
            },
        }
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result) as complete:
            conversation.with_user(self.user).action_send()
            conversation.with_user(self.user).write({
                'draft_prompt': 'Summarize this note',
                'draft_task': 'summarize',
            })
            conversation.with_user(self.user).action_send()
        self.assertEqual(len(conversation.message_ids), 4)
        self.assertEqual(conversation.message_ids[-1].usage['total_tokens'], 8)
        opened = conversation.with_user(self.user).assistant_open()
        self.assertEqual(opened['messages'][-1]['usage_summary'], {
            'input_tokens': 5,
            'output_tokens': 3,
            'reasoning_tokens': 2,
            'total_tokens': 8,
        })
        self.assertIn('First answer', complete.call_args.args[0])
        self.assertIn('Summarize', complete.call_args.args[0])
        self.assertFalse(conversation.draft_prompt)

    def test_provider_failure_is_recorded_without_success(self):
        conversation = self._conversation()
        conversation.with_user(self.user).draft_prompt = 'Hello'
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', side_effect=UserError('Provider unavailable')):
            conversation.with_user(self.user).action_send()
        self.assertEqual(conversation.message_ids.mapped('state'), ['failed', 'failed'])
        self.assertEqual(conversation.message_ids[-1].role, 'error')

    def test_assistant_markdown_is_rendered_without_unsafe_html_or_images(self):
        conversation = self._conversation().with_user(self.user)
        Message = self.env['qing.ai.message'].sudo()
        Message.create({
            'content': '**raw user text**', 'conversation_id': conversation.id,
            'role': 'user', 'state': 'done',
        })
        Message.create({
            'content': (
                '**Bold**\n\n- Item\n\n```python\nprint(1)\n```\n\n'
                '[Safe](https://example.com) [Relative](/web) '
                '[Unsafe](javascript:alert(1)) '
                '![Pixel](https://example.com/pixel.png) <script>alert(1)</script>'
            ),
            'conversation_id': conversation.id, 'role': 'assistant', 'state': 'done',
        })
        messages = conversation.assistant_open()['messages']
        self.assertIn('<strong>raw user text</strong>', messages[0]['content_html'])
        html = messages[1]['content_html']
        self.assertIn('<strong>Bold</strong>', html)
        self.assertIn('<li>', html)
        self.assertIn('<pre>', html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn('href="#"', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('href="javascript:', html)

    def test_translation_uses_selected_provider_and_target_language(self):
        conversation = self._conversation()
        conversation.with_user(self.user).write({
            'draft_prompt': 'Good morning',
            'draft_task': 'translate',
            'provider': 'openai',
            'target_language': 'Chinese',
        })
        result = {'text': '早上好', 'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result) as complete:
            conversation.with_user(self.user).action_send()
        self.assertEqual(complete.call_args.kwargs['provider'], 'openai')
        self.assertIn('Chinese', complete.call_args.args[0])
        self.assertEqual(conversation.message_ids[-1].content, '早上好')

    def test_daily_limit_blocks_another_paid_call(self):
        self.env['ir.config_parameter'].sudo().set_int('qing_ai.daily_request_limit', 1)
        conversation = self._conversation()
        result = {'text': 'OK', 'model': 'test', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result) as complete:
            conversation.with_user(self.user).draft_prompt = 'First'
            conversation.with_user(self.user).action_send()
            conversation.with_user(self.user).draft_prompt = 'Second'
            with self.assertRaises(UserError):
                conversation.with_user(self.user).action_send()
        self.assertEqual(complete.call_count, 1)

    def test_record_context_is_read_again_before_every_request(self):
        partner = self.env['res.partner'].create({'name': 'Verified Partner'})
        Conversation = self.env['qing.ai.conversation'].with_user(self.user)
        action = Conversation.action_open_for_record('res.partner', partner.id)
        conversation = Conversation.browse(action['params']['conversation_id'])
        conversation.assistant_select_agent(self.env.ref('qing_ai.agent_general').id)
        preview = conversation.assistant_open()
        self.assertIn('Verified Partner', [field['value'] for field in preview['context_fields']])
        conversation.draft_prompt = 'Summarize this customer'
        result = {'text': 'Summary', 'model': 'test', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result) as complete:
            conversation.action_send()
            self.assertIn('Verified Partner', complete.call_args.args[0])
            partner.unlink()
            conversation.draft_prompt = 'Again'
            with self.assertRaises(UserError):
                conversation.action_send()
        self.assertTrue(conversation.assistant_open()['context_unavailable'])
        self.assertEqual(complete.call_count, 1)

    def test_client_action_methods_respect_owner_and_return_reply(self):
        Conversation = self.env['qing.ai.conversation'].with_user(self.user)
        opened = Conversation.assistant_new(self.env.ref('qing_ai.agent_general').id)
        self.assertEqual(opened['name'], 'New Conversation')
        self.assertIn(opened['id'], [item['id'] for item in Conversation.assistant_list()])
        self.assertIn('default', [item['value'] for item in opened['providers']])
        other = self.env['qing.ai.conversation'].with_user(self.other_user)
        with self.assertRaises(AccessError):
            other.browse(opened['id']).assistant_open()
        result = {'text': 'Helpful reply', 'model': 'test-model', 'usage': {'total_tokens': 8}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result):
            reply = Conversation.browse(opened['id']).assistant_send('A question')
        self.assertEqual([message['role'] for message in reply['messages']], ['user', 'assistant'])
        self.assertEqual(reply['messages'][-1]['content'], 'Helpful reply')
        self.assertEqual(reply['name'], 'A question')
        self.assertTrue(Conversation.assistant_list()[0]['last_message_at'])

    def test_formatted_first_message_uses_plain_conversation_title(self):
        conversation = self._conversation().with_user(self.user)
        result = {'text': 'OK', 'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result):
            conversation.assistant_send('**Review** the attached files.')
        self.assertEqual(conversation.name, 'Review the attached files.')

    def test_client_action_rejects_unsupported_options(self):
        conversation = self._conversation().with_user(self.user)
        with self.assertRaises(UserError):
            conversation.assistant_send('Hello', task='execute')
        with self.assertRaises(UserError):
            conversation.assistant_send('Hello', provider='other_provider')
        with self.assertRaises(UserError):
            conversation.assistant_send('Hello', target_language='x' * 81)
        self.assertFalse(conversation.message_ids)

    def test_pasted_image_is_validated_stored_and_sent_only_with_owners_message(self):
        buffer = io.BytesIO()
        Image.new('RGB', (16, 16), 'red').save(buffer, format='PNG')
        image = {'name': 'clipboard.png', 'data': base64.b64encode(buffer.getvalue()).decode()}
        conversation = self._conversation().with_user(self.user)
        result = {'text': 'A red square', 'model': 'vision-test', 'usage': {}}
        with patch(
            'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
            return_value=result,
        ) as complete:
            opened = conversation.assistant_send('Describe this image', image=image)
        self.assertTrue(opened['messages'][0]['image_url'].startswith('/web/image/'))
        attachment = conversation.message_ids[0].image_attachment_id
        self.assertEqual(attachment.mimetype, 'image/png')
        self.assertFalse(attachment.public)
        self.assertEqual(complete.call_args.kwargs['image']['mimetype'], 'image/png')
        with self.assertRaises(AccessError):
            attachment.with_user(self.other_user).read(['raw'])
        with self.assertRaises(UserError):
            conversation.assistant_send('Bad image', image={
                'name': 'fake.png', 'data': base64.b64encode(b'not an image').decode(),
            })

    def test_pasted_text_file_is_extracted_saved_and_owner_private(self):
        conversation = self._conversation().with_user(self.user)
        document = {'name': 'notes.md', 'data': base64.b64encode(b'# Private plan\nConfirm dates').decode()}
        result = {'text': 'Dates need confirmation', 'model': 'test-model', 'usage': {}}
        with patch(
            'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
            return_value=result,
        ) as complete:
            opened = conversation.assistant_send('Summarize this', files=[document])
        self.assertIn('Confirm dates', complete.call_args.args[0])
        self.assertEqual(opened['messages'][0]['files'][0]['name'], 'notes.md')
        attachment = conversation.message_ids[0].file_attachment_ids
        self.assertFalse(attachment.public)
        self.assertEqual(attachment.res_model, 'qing.ai.message')
        with self.assertRaises(AccessError):
            attachment.with_user(self.other_user).read(['raw'])

    def test_attached_documents_reject_unsupported_or_oversized_content(self):
        conversation = self._conversation().with_user(self.user)
        for name, content in [
            ('script.py', b'print(1)'),
            ('fake.pdf', b'not a pdf'),
            ('large.txt', b'a' * 12001),
        ]:
            with self.subTest(name=name), self.assertRaises(UserError):
                conversation.assistant_send('Analyze', files=[{
                    'name': name, 'data': base64.b64encode(content).decode(),
                }])
        with self.assertRaises(UserError):
            conversation.assistant_send('Create a quotation', task='business', files=[{
                'name': 'brief.txt', 'data': base64.b64encode(b'brief').decode(),
            }])
        self.assertFalse(conversation.message_ids)

    def test_pdf_attachment_text_reaches_model_without_extra_erp_access(self):
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer)
        pdf.drawString(72, 720, 'Synthetic PDF note for AI')
        pdf.save()
        conversation = self._conversation().with_user(self.user)
        result = {'text': 'This is a synthetic note', 'model': 'test-model', 'usage': {}}
        with patch(
            'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
            return_value=result,
        ) as complete:
            conversation.assistant_send('Summarize the PDF', files=[{
                'name': 'note.pdf', 'data': base64.b64encode(buffer.getvalue()).decode(),
            }])
        self.assertIn('Synthetic PDF note for AI', complete.call_args.args[0])
        self.assertEqual(conversation.message_ids[0].file_attachment_ids.mimetype, 'application/pdf')
