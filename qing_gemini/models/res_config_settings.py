from odoo import fields, models

from .qing_ai_service import DEFAULT_ENDPOINT, DEFAULT_MODEL


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_ai_default_provider = fields.Selection(
        selection_add=[('gemini', 'Gemini')],
        ondelete={'gemini': 'set default'},
    )
    qing_gemini_api_key = fields.Char(
        string='Gemini API Key', config_parameter='qing_ai.gemini.api_key',
        groups='base.group_system',
    )
    qing_gemini_enabled = fields.Boolean(
        string='Enable Gemini', config_parameter='qing_ai.gemini.enabled',
        groups='base.group_system',
    )
    qing_gemini_image_enabled = fields.Boolean(
        string='Enable Image Input', config_parameter='qing_ai.gemini.image_enabled',
        default=True, groups='base.group_system',
    )
    qing_gemini_endpoint = fields.Char(
        string='Gemini API Base URL', config_parameter='qing_ai.gemini.endpoint',
        default=DEFAULT_ENDPOINT, groups='base.group_system',
    )
    qing_gemini_model = fields.Char(
        string='Gemini Model', config_parameter='qing_ai.gemini.model',
        default=DEFAULT_MODEL, groups='base.group_system',
    )

    def action_test_qing_gemini(self):
        self.ensure_one()
        service = self.env['qing.ai.service']
        return service._test_provider('gemini', service._provider_config('gemini'))
