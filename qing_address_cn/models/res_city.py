from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ResCity(models.Model):
    _inherit = 'res.city'

    _qing_cn_code_unique = models.Constraint(
        'UNIQUE(qing_cn_code)', 'A China city administrative code must be unique.',
    )

    @api.constrains('qing_cn_code', 'country_id', 'state_id')
    def _check_qing_cn_code(self):
        for city in self:
            if not city.qing_cn_code:
                continue
            if (
                city.country_id.code != 'CN'
                or len(city.qing_cn_code) != 6
                or not city.qing_cn_code.isascii()
                or not city.qing_cn_code.isdigit()
                or not city.qing_cn_code.endswith('00')
                or not city.state_id.qing_cn_code
                or not city.qing_cn_code.startswith(city.state_id.qing_cn_code)
            ):
                raise ValidationError(self.env._('A China city requires a six-digit city-level code and its matching province.'))

    def _qing_cn_check_maintenance(self):
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only an administrator can maintain the China address catalog.'))

    qing_cn_code = fields.Char(string='China Administrative Code', copy=False, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if any(values.get('qing_cn_code') for values in vals_list):
            self._qing_cn_check_maintenance()
        return super().create(vals_list)

    def write(self, values):
        if 'qing_cn_code' in values or any(self.mapped('qing_cn_code')):
            self._qing_cn_check_maintenance()
        return super().write(values)

    def unlink(self):
        if any(self.mapped('qing_cn_code')):
            self._qing_cn_check_maintenance()
        return super().unlink()
