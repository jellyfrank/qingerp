from odoo import api, fields, models

ADDRESS_FIELDS = ('country_id', 'state_id', 'city_id', 'city', 'zip', 'qing_cn_district_id', 'qing_cn_subdistrict_id')


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.depends('country_id', 'state_id', 'partner_id.country_id.code', 'partner_id.state_id.qing_cn_code')
    def _compute_qing_cn_address_enabled(self):
        for company in self:
            company.qing_cn_address_enabled = company.country_id.code == 'CN' and (
                not company.state_id or bool(company.state_id.qing_cn_code)
            )

    def _qing_cn_onchange_address(self, field_name):
        Partner = self.env['res.partner']
        values = {name: self[name].id if self._fields[name].type == 'many2one' else self[name] for name in ADDRESS_FIELDS}
        current = Partner.new(values)
        self.update(Partner._qing_cn_prepare_address_values({field_name: values[field_name]}, current=current))

    @api.onchange('country_id')
    def _onchange_qing_cn_country(self):
        self._qing_cn_onchange_address('country_id')

    @api.onchange('state_id')
    def _onchange_qing_cn_state(self):
        self._qing_cn_onchange_address('state_id')

    @api.onchange('city_id')
    def _onchange_qing_cn_city(self):
        self._qing_cn_onchange_address('city_id')

    @api.onchange('qing_cn_district_id')
    def _onchange_qing_cn_district(self):
        self._qing_cn_onchange_address('qing_cn_district_id')

    @api.onchange('qing_cn_subdistrict_id')
    def _onchange_qing_cn_subdistrict(self):
        self._qing_cn_onchange_address('qing_cn_subdistrict_id')

    city_id = fields.Many2one(related='partner_id.city_id', readonly=False)
    qing_cn_address_enabled = fields.Boolean(compute='_compute_qing_cn_address_enabled')
    qing_cn_city_code = fields.Char(related='city_id.qing_cn_code')
    qing_cn_district_id = fields.Many2one(related='partner_id.qing_cn_district_id', readonly=False)
    qing_cn_subdistrict_id = fields.Many2one(related='partner_id.qing_cn_subdistrict_id', readonly=False)

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        prepared = []
        for values in vals_list:
            values = dict(values)
            address = {name: values[name] for name in ADDRESS_FIELDS if name in values}
            if address:
                partner = self.env['res.partner'].browse(values.get('partner_id'))
                address = self.env['res.partner']._qing_cn_prepare_address_values(address, current=partner)
                if partner:
                    partner.write(address)
                values.update(address)
            prepared.append(values)
        return super().create(prepared)

    def write(self, values):
        address = {name: values[name] for name in ADDRESS_FIELDS if name in values}
        if address:
            self.check_access('write')
            # Native company inverses write one field at a time. Apply the whole hierarchy first.
            self.partner_id.write(address)
        return super().write(values)
