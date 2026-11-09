from odoo import api, fields, models
from odoo.exceptions import ValidationError


class Subdistrict(models.Model):
    _name = 'qing.address.cn.subdistrict'
    _description = 'China Subdistrict'
    _order = 'code, id'
    _rec_names_search = ('name', 'code')

    _code_unique = models.Constraint('UNIQUE(code)', 'A China subdistrict administrative code must be unique.')

    @api.constrains('code', 'district_id')
    def _check_hierarchy(self):
        for subdistrict in self:
            if (
                len(subdistrict.code) != 9
                or not subdistrict.code.isascii() or not subdistrict.code.isdigit()
                or not subdistrict.code.startswith(subdistrict.district_id.code)
            ):
                raise ValidationError(self.env._('The subdistrict administrative code must belong to its selected district.'))

    city_id = fields.Many2one(related='district_id.city_id', store=True, index=True)
    code = fields.Char(string='Administrative Code', required=True, index=True)
    country_id = fields.Many2one(related='district_id.country_id', store=True, index=True)
    district_id = fields.Many2one('qing.address.cn.district', string='District / County',
                                 required=True, index=True, ondelete='restrict')
    name = fields.Char(string='Name', required=True)
    state_id = fields.Many2one(related='district_id.state_id', store=True, index=True)
