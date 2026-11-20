from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingPublicAssistant(TransactionCase):

    def test_private_chat_replies_with_public_source(self):
        reader = new_test_user(self.env, login='assistant-public-reader')
        root = self.env.ref('base.partner_root')
        chat = reader.with_user(reader).with_context(lang='en_US')._init_odoobot()
        question = chat.message_post(
            body='The user cannot access an app. Where should I check their access rights?',
            subtype_xmlid='mail.mt_comment',
            message_type='comment',
        )
        reply = chat.message_ids.filtered(lambda message: message.id > question.id and message.author_id == root)
        self.assertEqual(len(reply), 1)
        self.assertIn('https://doc.qingerp.com/', reply.body)
        self.assertNotIn('/odoo/action-', reply.body)

    def test_general_question_without_provider_explains_chat_is_unavailable(self):
        reader = new_test_user(self.env, login='assistant-off-topic')
        root = self.env.ref('base.partner_root')
        chat = reader.with_user(reader).with_context(lang='en_US')._init_odoobot()
        question = chat.message_post(
            body='What is the weather forecast for Saturn?', subtype_xmlid='mail.mt_comment', message_type='comment',
        )
        reply = chat.message_ids.filtered(lambda message: message.id > question.id and message.author_id == root)
        self.assertEqual(len(reply), 1)
        self.assertIn('There is no available chat model configured here', reply.body)
        self.assertNotIn('https://doc.qingerp.com/', reply.body)

    def test_common_inventory_phrasings_choose_the_operation(self):
        manual = self.env['qing.public.manual']
        with patch.object(type(manual), '_installed_modules', return_value={'stock'}):
            for question, topic in (
                ('这个仓库怎么盘点库存？', 'stock_count'),
                ('仓库之间怎么移货？', 'warehouse_transfer'),
                ('怎么从一个库位转移到另一个库位？', 'warehouse_transfer'),
            ):
                with self.subTest(question=question):
                    section, _question = manual._match_question(question)
                    self.assertEqual(section['topic'], topic)

    def test_common_english_word_alone_does_not_trigger_a_manual_reply(self):
        manual = self.env['qing.public.manual']
        with patch.object(type(manual), '_installed_modules', return_value={'sale_management', 'qing_base'}):
            section, _question = manual._match_question('Where can I order a birthday cake?')
            self.assertIsNone(section)
