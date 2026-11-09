from odoo import fields, models
from odoo.exceptions import AccessError


PRICING_MODES = [('sale', 'Linked Quotation'), ('direct', 'Direct Pricing')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    qing_repair_auto_invoice = fields.Boolean(
        string='Create Draft Invoice on Repair Completion', default=False,
        help='Direct-priced repairs only. Creates a draft customer invoice; never posts it or records a payment.',
    )
    qing_repair_pricing_mode = fields.Selection(
        PRICING_MODES, string='Default Repair Pricing', default='sale', required=True,
    )

    def write(self, vals):
        if {'qing_repair_pricing_mode', 'qing_repair_auto_invoice'} & vals.keys() and not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can change the default repair pricing.'))
        return super().write(vals)
