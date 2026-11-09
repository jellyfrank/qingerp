from odoo import models


DEFAULT_ENDPOINT = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
DEFAULT_MODEL = 'glm-4-flash'


class QingAiService(models.AbstractModel):
    _inherit = 'qing.ai.service'

    def _provider_codes(self):
        return (*super()._provider_codes(), 'glm')

    def _native_tool_provider_codes(self):
        return (*super()._native_tool_provider_codes(), 'glm')

    def _complete_glm(self, prompt, config, max_output_tokens, image=None, **options):
        return self._chat_completion(prompt, config, max_output_tokens, image=image, **options)
