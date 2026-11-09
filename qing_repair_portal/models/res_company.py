from odoo import fields, models
from odoo.exceptions import AccessError


class ResCompany(models.Model):
    _inherit = 'res.company'

    qing_repair_acceptance_enabled = fields.Boolean(string='Repair Acceptance Signature')
    qing_repair_price_signature_enabled = fields.Boolean(string='Direct Price Confirmation Signature')

    def write(self, vals):
        settings = {'qing_repair_acceptance_enabled', 'qing_repair_price_signature_enabled'}
        if settings & vals.keys() and not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can change repair signature settings.'))
        return super().write(vals)
