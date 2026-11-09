from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    qing_partner_enforce_roles = fields.Boolean(
        string='Require enabled business roles',
        help='Require enabled customer/vendor roles on new sales, purchases and invoices. Enable after reviewing historical roles.',
    )

    def write(self, values):
        result = super().write(values)
        if 'qing_partner_enforce_roles' in values:
            self.env.invalidate_all()
        return result
