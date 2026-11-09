from odoo import api, fields, models
from odoo.exceptions import AccessError

ACCESS_MODES = [('shared', 'Shared basic contacts'), ('restricted', 'Restrict by business scope')]
SCOPES = [('none', 'None'), ('own', 'Own and explicitly shared'), ('team', 'Own, team and shared'), ('all', 'All in allowed companies')]
POLICY_FIELDS = {'qing_partner_access_mode', 'qing_customer_scope', 'qing_supplier_scope'}


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _qing_check_policy_write(self, values):
        if POLICY_FIELDS.intersection(values) and not (
            self.env.su or self.env.user.has_group('base.group_system')
        ):
            raise AccessError(self.env._('Only a system administrator can change partner access policies.'))

    qing_customer_scope = fields.Selection(SCOPES, string='Customer Scope', default='none', required=True, groups='base.group_system')
    qing_partner_access_mode = fields.Selection(ACCESS_MODES, string='Partner Visibility', default='shared', required=True, groups='base.group_system')
    qing_supplier_scope = fields.Selection(SCOPES, string='Supplier Scope', default='none', required=True, groups='base.group_system')

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self._qing_check_policy_write(values)
        return super().create(vals_list)

    def write(self, values):
        self._qing_check_policy_write(values)
        result = super().write(values)
        if (POLICY_FIELDS | {'company_ids', 'share'}).intersection(values):
            self.env.invalidate_all()
        return result
