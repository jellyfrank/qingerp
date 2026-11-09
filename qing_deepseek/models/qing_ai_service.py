from odoo import models


DEFAULT_ENDPOINT = 'https://api.deepseek.com/chat/completions'
DEFAULT_MODEL = 'deepseek-chat'


class QingAiService(models.AbstractModel):
    _inherit = 'qing.ai.service'

    def _provider_codes(self):
        return (*super()._provider_codes(), 'deepseek')

    def _native_tool_provider_codes(self):
        return (*super()._native_tool_provider_codes(), 'deepseek')

    def _complete_deepseek(self, prompt, config, max_output_tokens, image=None, **options):
        return self._chat_completion(prompt, config, max_output_tokens, image=image, **options)
