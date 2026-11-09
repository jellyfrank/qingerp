from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_partner_enforce_roles = fields.Boolean(
        related='company_id.qing_partner_enforce_roles', readonly=False,
    )
