from odoo import fields, models

from .qing_ai_service import DEFAULT_ENDPOINT, DEFAULT_MODEL


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_ai_default_provider = fields.Selection(
        selection_add=[('openai', 'OpenAI')],
        ondelete={'openai': 'set default'},
    )
    qing_openai_api_key = fields.Char(
        string='OpenAI API Key', config_parameter='qing_ai.openai.api_key',
        groups='base.group_system',
    )
    qing_openai_enabled = fields.Boolean(
        string='Enable OpenAI', config_parameter='qing_ai.openai.enabled',
        groups='base.group_system',
    )
    qing_openai_image_enabled = fields.Boolean(
        string='Enable Image Input', config_parameter='qing_ai.openai.image_enabled',
        default=True, groups='base.group_system',
    )
    qing_openai_endpoint = fields.Char(
        string='OpenAI Endpoint', config_parameter='qing_ai.openai.endpoint',
        default=DEFAULT_ENDPOINT, groups='base.group_system',
    )
    qing_openai_model = fields.Char(
        string='OpenAI Model', config_parameter='qing_ai.openai.model',
        default=DEFAULT_MODEL, groups='base.group_system',
    )

    def action_test_qing_openai(self):
        self.ensure_one()
        service = self.env['qing.ai.service']
        return service._test_provider('openai', service._provider_config('openai'))
