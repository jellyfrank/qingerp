from odoo import fields, models

from .qing_ai_service import DEFAULT_ENDPOINT, DEFAULT_MODEL


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_ai_default_provider = fields.Selection(
        selection_add=[('glm', 'GLM')],
        ondelete={'glm': 'set default'},
    )
    qing_glm_api_key = fields.Char(
        string='GLM API Key', config_parameter='qing_ai.glm.api_key',
        groups='base.group_system',
    )
    qing_glm_enabled = fields.Boolean(
        string='Enable GLM', config_parameter='qing_ai.glm.enabled',
        groups='base.group_system',
    )
    qing_glm_image_enabled = fields.Boolean(
        string='Enable Image Input', config_parameter='qing_ai.glm.image_enabled',
        default=False, groups='base.group_system',
    )
    qing_glm_endpoint = fields.Char(
        string='GLM Endpoint', config_parameter='qing_ai.glm.endpoint',
        default=DEFAULT_ENDPOINT, groups='base.group_system',
    )
    qing_glm_model = fields.Char(
        string='GLM Model', config_parameter='qing_ai.glm.model',
        default=DEFAULT_MODEL, groups='base.group_system',
    )

    def action_test_qing_glm(self):
        self.ensure_one()
        service = self.env['qing.ai.service']
        return service._test_provider('glm', service._provider_config('glm'))
