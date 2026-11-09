from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ResCountryState(models.Model):
    _inherit = 'res.country.state'

    _qing_cn_code_unique = models.Constraint(
        'UNIQUE(qing_cn_code)', 'A China province administrative code must be unique.',
    )

    @api.constrains('qing_cn_code', 'country_id')
    def _check_qing_cn_code(self):
        for state in self:
            if state.qing_cn_code and (
                state.country_id.code != 'CN'
                or len(state.qing_cn_code) != 2
                or not state.qing_cn_code.isascii()
                or not state.qing_cn_code.isdigit()
            ):
                raise ValidationError(self.env._('A China province requires a two-digit administrative code.'))

    qing_cn_code = fields.Char(string='China Administrative Code', copy=False, index=True)

    def write(self, values):
        if 'qing_cn_code' in values and not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only an administrator can maintain the China address catalog.'))
        return super().write(values)
