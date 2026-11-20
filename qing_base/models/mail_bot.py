import re

from markupsafe import Markup

from odoo import models
from odoo.tools import html2plaintext

from .qing_public_manual import _normalize


MODEL_QUESTION = re.compile(
    r'(?:请问[，, ]*)?(?:你|您|小青)?(?:现在|目前|当前)?'
    r'(?:是|在用|用的是|用的|用|使用的?|运行的?)?'
    r'(?:哪[个個款种種]|什么|什麼)(?:ai|大|语言|語言)?模型(?:呢|呀|啊|吗|嗎)?'
    r'|(?:what|which) (?:ai |language )?model (?:are you(?: using)?|do you use|powers you)'
    r'|what is your (?:ai |language )?model'
)
GREETINGS = frozenset({'hi', 'hello', 'hey', '你好', '您好', '嗨', '在吗', '在嗎', '早上好', '晚上好'})
THANKS = frozenset({'thanks', 'thank you', '谢谢', '謝謝', '谢谢你', '謝謝你'})


class MailBot(models.AbstractModel):
    _inherit = 'mail.bot'

    def _qing_assistant_help(self):
        return Markup('%s') % self.env._(
            'Chat with me here. When you ask how to use QingERP, I will include the relevant public manual section.'
        )

    def _qing_assistant_local_reply(self, text):
        normalized = ' '.join(_normalize(text).split()).strip(' .!?。！？…~～')
        greeting = normalized.removeprefix('小青').removesuffix('小青').strip(' ,，')
        if greeting in GREETINGS:
            return Markup('%s') % self.env._('Hi! I am here. What would you like to talk about?')
        if normalized in THANKS:
            return Markup('%s') % self.env._("You're welcome!")
        if MODEL_QUESTION.fullmatch(normalized):
            return self._qing_assistant_model_reply()
        return False

    def _qing_assistant_model_reply(self):
        return self._qing_assistant_no_model()

    def _qing_assistant_no_model(self):
        return Markup('%s') % self.env._(
            'There is no available chat model configured here. I can still answer from the QingERP public manual.'
        )

    def _qing_assistant_manual_miss(self):
        return Markup('%s') % self.env._(
            'I could not find this in the public manual. Try naming the app and the operation, '
            'for example Sales Quotation or Inventory Count.'
        )

    def _qing_assistant_unmatched_reply(self, channel, message, text):
        if self.env['qing.public.manual']._looks_like_erp_question(text):
            return self._qing_assistant_manual_miss()
        return self._qing_assistant_no_model()

    def _qing_assistant_answer(self, channel, message):
        text = html2plaintext(message.body or '', include_references=False).strip()
        if not text:
            return self._qing_assistant_help()
        if local_reply := self._qing_assistant_local_reply(text):
            return local_reply
        manual = self.env['qing.public.manual']
        section, question = manual._match_question(text)
        if not section:
            return self._qing_assistant_unmatched_reply(channel, message, text)
        return self._qing_assistant_reply(channel, message, section, question)

    def _qing_assistant_reply(self, channel, message, section, question):
        return self.env['qing.public.manual']._format_reply(section)

    def _get_answer(self, channel, body, message, command=False):
        if channel.channel_type == 'chat' and self.env.ref('base.partner_root') in channel.channel_member_ids.partner_id:
            if not self.env.user._is_internal() or self.env.user.odoobot_state == 'disabled':
                return False
            if command:
                return self._qing_assistant_help()
            if message.author_id == self.env.user.partner_id:
                return self._qing_assistant_answer(channel, message)
            return False
        return super()._get_answer(channel, body, message, command=command)
