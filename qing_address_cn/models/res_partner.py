from collections import defaultdict
from copy import deepcopy

from lxml import etree

from odoo import api, fields, models
from odoo.exceptions import ValidationError

ADDRESS_RELATIONS = ('country_id', 'state_id', 'city_id', 'qing_cn_district_id', 'qing_cn_subdistrict_id')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _view_get_address(self, arch):
        if self.env.context.get('no_address_format'):
            return super()._view_get_address(arch)
        view = self.env.ref('qing_address_cn.view_china_address_form')
        # A company-level China template must not replace the foreign fallback.
        if self.env.company.country_id.address_view_id == view:
            arch = super(ResPartner, self.with_context(no_address_format=True))._view_get_address(arch)
        else:
            arch = super()._view_get_address(arch)
        template, _view = self.with_context(no_address_format=True)._get_view(view.id, 'form')
        china = template.xpath("//div[hasclass('o_address_format')]")[0]
        for address in arch.xpath("//div[hasclass('o_address_format')]"):
            original_invisible = address.get('invisible', 'False')
            # Modifiers depend on the edited contact, including unsaved child addresses.
            address.set('invisible', f"({original_invisible}) or country_code == 'CN'")
            if not address.xpath(".//field[@name='country_code']"):
                address.insert(0, etree.Element('field', name='country_code', invisible='1'))
            replacement = deepcopy(china)
            replacement.set('name', 'qing_cn_address')
            replacement.set('invisible', f"({original_invisible}) or country_code != 'CN'")
            address.addprevious(replacement)
        return arch

    @api.model
    def _address_fields(self):
        return super()._address_fields() + ['qing_cn_district_id', 'qing_cn_subdistrict_id']

    @api.depends('country_id.code', 'state_id.qing_cn_code')
    def _compute_qing_cn_address_enabled(self):
        for partner in self:
            partner.qing_cn_address_enabled = partner.country_id.code == 'CN' and (
                not partner.state_id or bool(partner.state_id.qing_cn_code)
            )

    @api.model
    def _qing_cn_city_text(self, city, district):
        if city.qing_cn_code and city.qing_cn_code[2] == '9':
            return district.name or False
        return city.name or False

    @api.model
    def _qing_cn_prepare_address_values(self, values, current=None):
        """Normalize the selected hierarchy for forms, imports and direct ORM writes."""
        prepared = dict(values)
        selected = {}
        for name in ADDRESS_RELATIONS:
            field = self._fields[name]
            model = self.env[field.comodel_name]
            selected[name] = model.browse(values[name]) if name in values else (current[name] if current else model)

        # A selected leaf can supply its parents, but never overwrite a conflicting explicit parent.
        parents = {}
        if values.get('qing_cn_subdistrict_id'):
            parents['qing_cn_district_id'] = selected['qing_cn_subdistrict_id'].district_id
        district = parents.get('qing_cn_district_id', selected['qing_cn_district_id'])
        if values.get('qing_cn_district_id') or values.get('qing_cn_subdistrict_id'):
            parents['city_id'] = district.city_id
        city = parents.get('city_id', selected['city_id'])
        if (values.get('city_id') and city.qing_cn_code) or 'city_id' in parents:
            parents.update({'state_id': city.state_id, 'country_id': city.country_id})
        state = parents.get('state_id', selected['state_id'])
        if values.get('state_id') or 'state_id' in parents:
            parents['country_id'] = state.country_id
        for name, record in parents.items():
            if name in values and selected[name] != record:
                raise ValidationError(self.env._('The selected China address levels do not belong to the same hierarchy.'))
            selected[name] = record
            prepared[name] = record.id

        country, state, city, district, subdistrict = (selected[name] for name in ADDRESS_RELATIONS)
        if 'country_id' in values and 'state_id' not in values and state.country_id != country:
            state = selected['state_id'] = self.env['res.country.state']
            prepared['state_id'] = False
        if city and (city.country_id != country or (country.code == 'CN' and city.state_id != state)):
            if values.get('city_id'):
                raise ValidationError(self.env._('The city must belong to the selected country and province.'))
            city = selected['city_id'] = self.env['res.city']
            prepared['city_id'] = False
        enabled = country.code == 'CN' and (not state or bool(state.qing_cn_code))
        if values.get('city_id') and enabled and not city.qing_cn_code:
            raise ValidationError(self.env._('Select a city from the China address catalog; select its district in the District / County field.'))
        if district and (not enabled or district.city_id != city):
            if values.get('qing_cn_district_id'):
                raise ValidationError(self.env._('The district must belong to the selected city.'))
            district = selected['qing_cn_district_id'] = self.env['qing.address.cn.district']
            prepared['qing_cn_district_id'] = False
        if subdistrict and (not enabled or subdistrict.district_id != district):
            if values.get('qing_cn_subdistrict_id'):
                raise ValidationError(self.env._('The subdistrict must belong to the selected district.'))
            prepared['qing_cn_subdistrict_id'] = False
        if city.qing_cn_code:
            expected_city = self._qing_cn_city_text(city, district)
            if 'city' in values and values['city'] and values['city'] != expected_city:
                raise ValidationError(self.env._('The city text must match the structured China address.'))
            prepared['city'] = expected_city
        elif 'city_id' in prepared and not city:
            prepared.setdefault('city', False)
            prepared.setdefault('zip', False)
        return prepared

    @api.constrains(*ADDRESS_RELATIONS, 'city')
    def _check_qing_cn_address(self):
        for partner in self:
            city = partner.city_id
            district = partner.qing_cn_district_id
            subdistrict = partner.qing_cn_subdistrict_id
            if city.qing_cn_code and (
                partner.country_id != city.country_id or partner.state_id != city.state_id
                or partner.city != self._qing_cn_city_text(city, district)
            ):
                raise ValidationError(self.env._('The city must belong to the selected country and province.'))
            if district and (not partner.qing_cn_address_enabled or district.city_id != city):
                raise ValidationError(self.env._('The district must belong to the selected city.'))
            if subdistrict and subdistrict.district_id != district:
                raise ValidationError(self.env._('The subdistrict must belong to the selected district.'))

    @api.onchange('country_id')
    def _onchange_country_id(self):
        super()._onchange_country_id()
        self.update(self._qing_cn_prepare_address_values({'country_id': self.country_id.id}, current=self))

    @api.onchange('state_id')
    def _onchange_state(self):
        super()._onchange_state()
        self.update(self._qing_cn_prepare_address_values({'state_id': self.state_id.id}, current=self))

    @api.onchange('city_id')
    def _onchange_city_id(self):
        if self.country_id.code != 'CN':
            super()._onchange_city_id()
        self.update(self._qing_cn_prepare_address_values({'city_id': self.city_id.id}, current=self))

    @api.onchange('qing_cn_district_id')
    def _onchange_qing_cn_district_id(self):
        self.update(self._qing_cn_prepare_address_values({'qing_cn_district_id': self.qing_cn_district_id.id}, current=self))

    @api.onchange('qing_cn_subdistrict_id')
    def _onchange_qing_cn_subdistrict_id(self):
        self.update(self._qing_cn_prepare_address_values({'qing_cn_subdistrict_id': self.qing_cn_subdistrict_id.id}, current=self))

    def _prepare_display_address(self, without_name=False):
        address_format, args = super()._prepare_display_address(without_name)
        if self.country_id.code == 'CN' and self.city_id.qing_cn_code:
            city = self.city_id
            city_name = city.name
            if city.name == self.state_id.name or city.qing_cn_code[2] == '9':
                city_name = ''
            args['city'] = city_name + (self.qing_cn_district_id.name or '') + (self.qing_cn_subdistrict_id.name or '')
        return address_format, args

    def _display_address_depends(self):
        return super()._display_address_depends() + [
            'city_id.name', 'qing_cn_district_id.name', 'qing_cn_subdistrict_id.name',
        ]

    qing_cn_address_enabled = fields.Boolean(compute='_compute_qing_cn_address_enabled')
    qing_cn_city_code = fields.Char(related='city_id.qing_cn_code')
    qing_cn_district_id = fields.Many2one('qing.address.cn.district', string='District / County',
                                        domain="[('city_id', '=', city_id)]", ondelete='restrict')
    qing_cn_subdistrict_id = fields.Many2one('qing.address.cn.subdistrict', string='Subdistrict / Township',
                                           domain="[('district_id', '=', qing_cn_district_id)]", ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        prepared = [self._qing_cn_prepare_address_values(values) if set(ADDRESS_RELATIONS).intersection(values)
                    else values for values in vals_list]
        return super().create(prepared)

    def write(self, values):
        if not (set(ADDRESS_RELATIONS) | {'city'}).intersection(values):
            return super().write(values)
        groups = defaultdict(list)
        group_values = {}
        for partner in self:
            prepared = self._qing_cn_prepare_address_values(values, current=partner)
            key = tuple((name, prepared.get(name)) for name in (*ADDRESS_RELATIONS, 'city', 'zip'))
            groups[key].append(partner.id)
            group_values[key] = prepared
        for key, ids in groups.items():
            super(ResPartner, self.browse(ids)).write(group_values[key])
        return True
