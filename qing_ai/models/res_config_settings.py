from odoo import fields, models

from .qing_ai_service import DEFAULT_MAX_OUTPUT_TOKENS
from .qing_ai_usage_daily import DEFAULT_DAILY_LIMIT


DEFAULT_PROVIDERS = [('none', 'Not configured')]


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_ai_daily_request_limit = fields.Integer(
        string='Daily AI Model Calls per User',
        config_parameter='qing_ai.daily_request_limit',
        default=DEFAULT_DAILY_LIMIT,
    )
    qing_ai_default_provider = fields.Selection(
        DEFAULT_PROVIDERS,
        string='Default AI Provider',
        config_parameter='qing_ai.default_provider',
        default='none',
    )
    qing_ai_max_output_tokens = fields.Integer(
        string='Maximum Output Tokens',
        config_parameter='qing_ai.max_output_tokens',
        default=DEFAULT_MAX_OUTPUT_TOKENS,
    )
