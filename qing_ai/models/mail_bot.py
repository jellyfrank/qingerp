import re

from markupsafe import Markup

from odoo import models
from odoo.tools import html2plaintext

from odoo.addons.qing_base.models.qing_public_manual import _normalize


# Requests for live records go to the AI agent even when a manual section looks similar.
RECORD_REQUEST = re.compile(
    r'多少|几[个位家张條条笔单次]|列出|列一下|查一?下|查查|查询|查找|找一?下|统计|汇总|有哪些|'
    r'最近|本[周月季年]|今[天日年]|昨天|上个?[周月]|去年|新建|创建|建一个|建个|添加|修改|更新|改成|删除|'
    r'\b(?:how many|how much|list|show me|count|find|look up|total|latest|recent|create|add|update)\b'
)
HOW_TO = re.compile(
    r'怎么|怎样|如何|在哪|哪里|步骤|流程|教程|\bhow (?:do|to|can|should)\b|\bwhere (?:is|can|do)\b'
)


def _asks_for_records(text):
    normalized = _normalize(text)
    return bool(RECORD_REQUEST.search(normalized)) and not HOW_TO.search(normalized)


class MailBot(models.AbstractModel):
    _inherit = 'mail.bot'

    def _qing_assistant_provider(self):
        service = self.env['qing.ai.service']
        provider = service._resolve_provider()
        if provider not in service._provider_codes() or not self.env['qing.ai.guide']._online_allowed():
            return None, {}
        config = service._provider_config(provider)
        if not config['enabled'] or not all(config[key] for key in ('api_key', 'endpoint', 'model')):
            return None, {}
        return provider, config

    def _qing_assistant_model_reply(self):
        provider, config = self._qing_assistant_provider()
        if not provider:
            return super()._qing_assistant_model_reply()
        return Markup('%s') % self.env._(
            'The configured chat model is %(model)s.', model=config['model'],
        )

    def _qing_assistant_agent_reply(self, channel, message):
        """
        Answer through the user's AI agent when Qing Assistant may query company data.

        The agent acts as the user who wrote the message, so the reply only uses records that
        user may access.

        :return: True when the reply was queued for the agent
        """
        owner = channel._qing_ai_assistant_author(message)
        if owner != self.env.user or not self._qing_assistant_provider()[0]:
            return False
        if not channel.sudo()._qing_ai_bind_assistant(owner):
            return False
        self.env['qing.ai.discuss.job']._enqueue(channel.sudo(), message)
        return True

    def _qing_assistant_answer(self, channel, message):
        text = html2plaintext(message.body or '', include_references=False).strip()
        if (text and not self._qing_assistant_local_reply(text) and _asks_for_records(text)
                and self._qing_assistant_agent_reply(channel, message)):
            return False
        return super()._qing_assistant_answer(channel, message)

    def _qing_assistant_unmatched_reply(self, channel, message, text):
        if self._qing_assistant_agent_reply(channel, message):
            return False
        if self.env['qing.public.manual']._looks_like_erp_question(text):
            return self._qing_assistant_manual_miss()
        provider, _config = self._qing_assistant_provider()
        if not provider or channel._qing_ai_assistant_author(message) != self.env.user:
            return self._qing_assistant_no_model()
        self.env['qing.ai.discuss.job']._enqueue(channel.sudo(), message, general_chat=True)
        return False

    def _qing_assistant_reply(self, channel, message, section, question):
        if question and channel._qing_ai_assistant_author(message) == self.env.user:
            provider, _config = self._qing_assistant_provider()
            if provider:
                self.env['qing.ai.discuss.job']._enqueue(channel.sudo(), message, manual=True)
                return False
        return super()._qing_assistant_reply(channel, message, section, question)
