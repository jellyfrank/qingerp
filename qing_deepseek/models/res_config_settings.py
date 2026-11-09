from odoo import fields, models

from .qing_ai_service import DEFAULT_ENDPOINT, DEFAULT_MODEL


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_ai_default_provider = fields.Selection(
        selection_add=[('deepseek', 'DeepSeek')],
        ondelete={'deepseek': 'set default'},
    )
    qing_deepseek_api_key = fields.Char(
        string='DeepSeek API Key', config_parameter='qing_ai.deepseek.api_key',
        groups='base.group_system',
    )
    qing_deepseek_enabled = fields.Boolean(
        string='Enable DeepSeek', config_parameter='qing_ai.deepseek.enabled',
        groups='base.group_system',
    )
    qing_deepseek_image_enabled = fields.Boolean(
        string='Enable Image Input', config_parameter='qing_ai.deepseek.image_enabled',
        default=False, groups='base.group_system',
    )
    qing_deepseek_endpoint = fields.Char(
        string='DeepSeek Endpoint', config_parameter='qing_ai.deepseek.endpoint',
        default=DEFAULT_ENDPOINT, groups='base.group_system',
    )
    qing_deepseek_model = fields.Char(
        string='DeepSeek Model', config_parameter='qing_ai.deepseek.model',
        default=DEFAULT_MODEL, groups='base.group_system',
    )

    def action_test_qing_deepseek(self):
        self.ensure_one()
        service = self.env['qing.ai.service']
        return service._test_provider('deepseek', service._provider_config('deepseek'))
