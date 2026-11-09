from odoo import api, fields, models
from odoo.exceptions import ValidationError


class District(models.Model):
    _name = 'qing.address.cn.district'
    _description = 'China District'
    _order = 'code, id'
    _rec_names_search = ('name', 'code')

    _code_unique = models.Constraint('UNIQUE(code)', 'A China district administrative code must be unique.')

    @api.constrains('code', 'city_id')
    def _check_hierarchy(self):
        for district in self:
            city_code = district.city_id.qing_cn_code or ''
            prefix = city_code[:2] if city_code.endswith('0000') else city_code[:4]
            if (
                not city_code or len(district.code) != 6
                or not district.code.isascii() or not district.code.isdigit()
                or not district.code.startswith(prefix)
            ):
                raise ValidationError(self.env._('The district administrative code must belong to its selected city.'))

    city_id = fields.Many2one('res.city', string='City', required=True, index=True, ondelete='restrict',
                             domain="[('qing_cn_code', '!=', False)]")
    code = fields.Char(string='Administrative Code', required=True, index=True)
    country_id = fields.Many2one(related='city_id.country_id', store=True, index=True)
    name = fields.Char(string='Name', required=True)
    state_id = fields.Many2one(related='city_id.state_id', store=True, index=True)
