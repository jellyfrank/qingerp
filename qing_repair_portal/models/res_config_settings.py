from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_repair_acceptance_enabled = fields.Boolean(related='company_id.qing_repair_acceptance_enabled', readonly=False)
    qing_repair_price_signature_enabled = fields.Boolean(related='company_id.qing_repair_price_signature_enabled', readonly=False)
