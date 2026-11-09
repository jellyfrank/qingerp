"""Regression coverage for Odoo 20 code translation loading."""

from pathlib import Path

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged
from odoo.tools.translate import CodeTranslations, code_translations


@tagged('post_install', '-at_install')
class TestQingAiTranslations(TransactionCase):

    def test_chinese_code_translations_load(self):
        path = Path(get_module_path('qing_ai')) / 'i18n' / 'zh_CN.po'
        with path.open('rb') as source:
            translations = CodeTranslations._read_code_translations_file(
                source, lambda row: True
            )
        self.assertEqual(
            translations['Enter a message for the AI assistant.'],
            '请输入发送给 AI 助手的消息。',
        )
        self.assertEqual(
            translations['The AI request failed. Please try again.'],
            'AI 请求失败，请重试。',
        )
        self.assertEqual(
            translations['The AI request failed. Please try again or open Qing AI for details.'],
            'AI 请求失败，请重试或打开青云 AI 查看详情。',
        )
        self.assertEqual(
            code_translations.get_python_translations('qing_ai', 'zh_CN')[
                'Enter a message for the AI assistant.'
            ],
            '请输入发送给 AI 助手的消息。',
        )
        web_messages = code_translations.get_web_translations('qing_ai', 'zh_CN')['messages']
        self.assertIn(
            {'id': 'The AI request failed. Please try again.', 'string': 'AI 请求失败，请重试。'},
            web_messages,
        )
        self.assertIn(
            {'id': 'Open in Discuss', 'string': '在讨论中打开'},
            web_messages,
        )
        self.assertTrue(self.env['ir.http']._get_web_translations_hash(('qing_ai',), 'zh_CN'))
