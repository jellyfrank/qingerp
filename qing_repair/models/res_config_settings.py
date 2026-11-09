from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_repair_auto_invoice = fields.Boolean(
        related='company_id.qing_repair_auto_invoice', readonly=False,
    )
    qing_repair_pricing_mode = fields.Selection(
        related='company_id.qing_repair_pricing_mode', readonly=False,
    )
