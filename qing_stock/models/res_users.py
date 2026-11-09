from odoo import api, fields, models
from odoo.exceptions import AccessError


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _qing_check_negative_stock_permission_write(self, values):
        if 'qing_allow_negative_stock' in values and not (
            self.env.su or self.env.user.has_group('base.group_system')
        ):
            raise AccessError(self.env._('Only a system administrator can change negative stock permission.'))

    qing_allow_negative_stock = fields.Boolean(
        string='Allow Negative Stock',
        help='Allow this user to complete stock operations that make an internal or transit location negative.',
        groups='base.group_system',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self._qing_check_negative_stock_permission_write(values)
        return super().create(vals_list)

    def write(self, values):
        self._qing_check_negative_stock_permission_write(values)
        return super().write(values)
