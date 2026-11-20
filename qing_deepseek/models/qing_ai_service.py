from odoo import models


DEFAULT_ENDPOINT = 'https://api.deepseek.com/chat/completions'
DEFAULT_MODEL = 'deepseek-chat'
THINKING_MODE_MODELS = {'deepseek-flash', 'deepseek-v4-pro'}


class QingAiService(models.AbstractModel):
    _inherit = 'qing.ai.service'

    def _provider_codes(self):
        return (*super()._provider_codes(), 'deepseek')

    def _native_tool_provider_codes(self):
        return (*super()._native_tool_provider_codes(), 'deepseek')

    def _guide_completion_config(self, provider, config):
        config = super()._guide_completion_config(provider, config)
        if (
            provider == 'deepseek'
            and config['model'] in THINKING_MODE_MODELS
            and config['endpoint'].startswith('https://api.deepseek.com/')
        ):
            # In thinking mode, reasoning can use the guide's token budget before any answer is visible.
            return dict(config, thinking={'type': 'disabled'})
        return config

    def _complete_deepseek(self, prompt, config, max_output_tokens, image=None, **options):
        return self._chat_completion(prompt, config, max_output_tokens, image=image, **options)
