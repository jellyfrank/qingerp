from lxml import etree

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged, new_test_user, Form
from odoo.addons.qing_base.tools import strict_computes
from odoo.tools.safe_eval import safe_eval

from ..hooks import convert_legacy_addresses


@tagged('post_install', '-at_install')
class TestChinaAddress(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']
        cls.cn = cls.env.ref('base.cn')
        cls.sd = cls.env.ref('base.state_cn_SD')
        cls.zj = cls.env.ref('base.state_cn_ZJ')
        cls.qd = cls.env.ref('qing_address_cn.city_370200')
        cls.jn = cls.env.ref('qing_address_cn.city_370100')
        cls.shibei = cls.env.ref('qing_address_cn.district_370203')
        cls.subdistrict = cls.env['qing.address.cn.subdistrict'].search([('district_id', '=', cls.shibei.id)], limit=1)
        cls.partner = cls.Partner.create({'name': 'China Address Fixture', 'qing_cn_subdistrict_id': cls.subdistrict.id,
                                         'street': 'Test Road 88', 'zip': '266000'})

    def test_catalog_hierarchy_and_counts(self):
        self.assertEqual(self.env['res.country.state'].search_count([('qing_cn_code', '!=', False)]), 31)
        self.assertEqual(self.env['res.city'].search_count([('qing_cn_code', '!=', False)]), 341)
        self.assertEqual(self.env['qing.address.cn.district'].search_count([]), 2843)
        self.assertEqual(self.env['qing.address.cn.subdistrict'].search_count([]), 41001)
        self.assertEqual(self.shibei.city_id, self.qd)
        self.assertEqual(self.subdistrict.district_id, self.shibei)
        repeated = self.env['qing.address.cn.district'].search([('name', '=', '鼓楼区')])
        self.assertGreater(len(repeated), 1)
        self.assertGreater(len(repeated.mapped('city_id')), 1)

    def test_leaf_selection_infers_ancestors(self):
        self.assertEqual(self.partner.country_id, self.cn)
        self.assertEqual(self.partner.state_id, self.sd)
        self.assertEqual(self.partner.city_id, self.qd)
        self.assertEqual(self.partner.qing_cn_district_id, self.shibei)
        self.assertEqual(self.partner.city, '青岛市')

    def test_province_write_clears_children(self):
        self.partner.write({'state_id': self.zj.id})
        self.assertEqual(self.partner.state_id, self.zj)
        self.assertFalse(self.partner.city_id)
        self.assertFalse(self.partner.qing_cn_district_id)
        self.assertFalse(self.partner.qing_cn_subdistrict_id)
        self.assertFalse(self.partner.city)
        self.assertFalse(self.partner.zip)

    def test_province_onchange_keeps_province(self):
        record = self.Partner.new(self.partner._convert_fields_to_values(self.partner._address_fields()))
        record.state_id = self.zj
        record._onchange_state()
        record._onchange_city_id()
        self.assertEqual(record.state_id, self.zj)
        self.assertFalse(record.city_id)
        self.assertFalse(record.qing_cn_district_id)
        self.assertFalse(record.qing_cn_subdistrict_id)

    def test_city_write_clears_wrong_district(self):
        self.partner.write({'city_id': self.jn.id})
        self.assertEqual(self.partner.state_id, self.sd)
        self.assertEqual(self.partner.city, '济南市')
        self.assertFalse(self.partner.qing_cn_district_id)
        self.assertFalse(self.partner.qing_cn_subdistrict_id)

    def test_clear_city_keeps_country_and_province(self):
        record = self.Partner.new(self.partner._convert_fields_to_values(self.partner._address_fields()))
        record.city_id = False
        record._onchange_city_id()
        self.assertEqual(record.country_id, self.cn)
        self.assertEqual(record.state_id, self.sd)
        self.assertFalse(record.qing_cn_district_id)

    def test_district_write_clears_wrong_subdistrict(self):
        shinan = self.env.ref('qing_address_cn.district_370202')
        self.partner.write({'qing_cn_district_id': shinan.id})
        self.assertEqual(self.partner.city_id, self.qd)
        self.assertFalse(self.partner.qing_cn_subdistrict_id)

    def test_reject_conflicting_explicit_ancestors(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Partner.create({'name': 'Invalid Address', 'state_id': self.zj.id, 'qing_cn_district_id': self.shibei.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.partner.write({'city_id': self.jn.id, 'qing_cn_district_id': self.shibei.id})

    def test_reject_wrong_city_text(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.partner.write({'city': 'Wrong City'})

    def test_parent_address_inheritance(self):
        child = self.Partner.create({'name': 'Child Fixture', 'parent_id': self.partner.id, 'type': 'contact'})
        self.assertEqual(child.city_id, self.qd)
        self.assertEqual(child.qing_cn_district_id, self.shibei)
        self.assertEqual(child.qing_cn_subdistrict_id, self.subdistrict)
        self.partner.write({'city_id': self.jn.id})
        self.assertEqual(child.city_id, self.jn)
        self.assertFalse(child.qing_cn_district_id)
        self.assertFalse(child.qing_cn_subdistrict_id)

    def test_foreign_country_uses_native_address(self):
        us = self.env.ref('base.us')
        self.partner.write({'country_id': us.id, 'city': 'Seattle', 'zip': '98101'})
        self.assertFalse(self.partner.qing_cn_address_enabled)
        self.assertFalse(self.partner.city_id)
        self.assertFalse(self.partner.qing_cn_district_id)
        self.assertFalse(self.partner.qing_cn_subdistrict_id)
        self.assertEqual(self.partner.city, 'Seattle')
        self.assertEqual(self.partner.zip, '98101')

    def test_non_mainland_china_city_text(self):
        partner = self.Partner.create({'name': 'HK Fixture', 'country_id': self.cn.id,
                                      'state_id': self.env.ref('base.state_cn_HK').id, 'city': '香港'})
        self.assertFalse(partner.qing_cn_address_enabled)
        self.assertEqual(partner.city, '香港')

    def test_form_domains_and_province_first(self):
        self.env.company.country_id = self.cn
        view = self.Partner.get_view(self.env.ref('base.view_partner_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        address = arch.xpath("//div[contains(@class, 'o_address_format')]")[0]
        fields = [node.get('name') for node in address.xpath('.//field')]
        self.assertLess(fields.index('state_id'), fields.index('city_id'))
        self.assertIn('qing_cn_district_id', fields)
        self.assertIn('qing_cn_subdistrict_id', fields)
        city_node = address.xpath(".//field[@name='city_id']")[0]
        domain = safe_eval(city_node.get('domain'), {'country_id': self.cn.id, 'state_id': self.sd.id,
                                                    'qing_cn_address_enabled': True})
        cities = self.env['res.city'].search(domain)
        self.assertIn(self.qd, cities)
        self.assertTrue(all(city.state_id == self.sd and city.qing_cn_code for city in cities))
        self.assertFalse(any('市北区' in city.name for city in cities))
        child = arch.xpath("//field[@name='child_ids']//form//field[@name='qing_cn_district_id']")
        self.assertTrue(child)

    def test_address_display_includes_leaf_levels(self):
        address = self.partner._display_address()
        self.assertIn('山东省', address)
        self.assertIn('青岛市', address)
        self.assertIn('市北区', address)
        self.assertIn(self.subdistrict.name, address)
        self.assertIn('Test Road 88', address)

    def test_municipality_does_not_repeat_city(self):
        partner = self.Partner.create({'name': 'Beijing Fixture',
                                      'qing_cn_district_id': self.env.ref('qing_address_cn.district_110101').id})
        self.assertEqual(partner._display_address().count('北京市'), 1)
        self.assertIn('东城区', partner._display_address())

    def test_province_direct_county_is_not_a_city(self):
        partner = self.Partner.create({'name': 'Direct County Fixture',
                                      'qing_cn_district_id': self.env.ref('qing_address_cn.district_469021').id})
        self.assertEqual(partner.city_id, self.env.ref('qing_address_cn.city_469000'))
        self.assertEqual(partner.city, '定安县')
        self.assertEqual(partner._display_address().count('定安县'), 1)
        self.assertNotIn('直辖县级行政区划', partner._display_address())

    def test_catalog_read_only_for_ordinary_user(self):
        user = self.env['res.users'].create({'name': 'Address Reader', 'login': 'address_reader_fixture',
                                           'group_ids': [self.env.ref('base.group_user').id]})
        self.shibei.with_user(user).check_access('read')
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.shibei.with_user(user).write({'name': 'Wrong'})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.qd.with_user(user).write({'name': 'Wrong'})

    def test_company_address_uses_partner_hierarchy(self):
        company = self.env['res.company'].create({'name': 'Address Company Fixture'})
        company.write({'qing_cn_subdistrict_id': self.subdistrict.id})
        self.assertEqual(company.partner_id.city_id, self.qd)
        self.assertEqual(company.partner_id.qing_cn_district_id, self.shibei)
        self.assertEqual(company.state_id, self.sd)
        self.assertEqual(company.city, '青岛市')

    def test_legacy_conversion_preserves_details_and_is_repeatable(self):
        City = self.env['res.city']
        legacy = City.create({'name': '青岛市市北区', 'state_id': self.sd.id, 'country_id': self.cn.id, 'zipcode': '266000'})
        unmatched = City.create({'name': '已撤销的旧县区', 'state_id': self.sd.id, 'country_id': self.cn.id})
        for index, city in enumerate(legacy | unmatched):
            self.env['ir.model.data'].create({'module': 'l10n_cn', 'name': f'qing_address_legacy_fixture_{index}',
                                             'model': 'res.city', 'res_id': city.id})
        partners = self.Partner.create([{'name': 'Legacy Address Fixture', 'country_id': self.cn.id,
                                        'state_id': self.sd.id, 'street': 'Original street', 'zip': '266000'}
                                       for _ in range(3)])
        partners[2].state_id = self.zj
        for partner, city in zip(partners, [legacy, unmatched, legacy]):
            self.cr.execute('UPDATE res_partner SET city_id=%s, city=%s WHERE id=%s', (city.id, city.name, partner.id))
        partners.invalidate_recordset(['city_id', 'city'])
        report = convert_legacy_addresses(self.env)
        self.assertEqual(report, {'converted': 1, 'unresolved': 2})
        self.assertEqual(partners[0].city_id, self.qd)
        self.assertEqual(partners[0].qing_cn_district_id, self.shibei)
        self.assertEqual(partners[0].zip, '266000')
        self.assertEqual(partners[0].street, 'Original street')
        self.assertEqual(partners[1].city_id, unmatched)
        self.assertEqual(partners[2].state_id, self.zj)
        self.assertEqual(partners[2].city_id, legacy)
        self.assertEqual(convert_legacy_addresses(self.env), {'converted': 0, 'unresolved': 2})

    def test_partner_editor_can_create_structured_address(self):
        if 'qing_api_uid' not in self.Partner._fields:
            self.skipTest('Optional qing_partner integration is not installed.')
        user = new_test_user(self.env, login='address_editor_fixture', groups='qing_partner.group_partner_editor')
        partner = self.Partner.with_user(user).create({'name': 'Editor Address Fixture',
                                                     'qing_cn_subdistrict_id': self.subdistrict.id})
        self.assertEqual(partner.city_id, self.qd)
        self.assertEqual(partner.qing_cn_district_id, self.shibei)
        with self.assertRaises(AccessError), self.cr.savepoint():
            partner.write({'qing_cn_district_id': self.env.ref('qing_address_cn.district_370202').id})

    def test_company_form_changes_city_and_province_atomically(self):
        # Python Form consumes model fields, not the browser read metadata.
        company = self.env['res.company'].create({'name': 'Company Form Address Fixture',
                                                'qing_cn_district_id': self.shibei.id})
        with strict_computes(), Form(company, view=self.env.ref('base.view_company_form')) as form:
            form.city_id = self.jn
        self.assertEqual(company.city_id, self.jn)
        self.assertEqual(company.city, '济南市')
        self.assertFalse(company.qing_cn_district_id)
        with strict_computes(), Form(company, view=self.env.ref('base.view_company_form')) as form:
            form.state_id = self.zj
        self.assertEqual(company.state_id, self.zj)
        self.assertFalse(company.city_id)
        self.assertFalse(company.qing_cn_district_id)

    def test_province_infers_country_and_rejects_conflict(self):
        partner = self.Partner.create({'name': 'Province Import Fixture', 'state_id': self.sd.id})
        self.assertEqual(partner.country_id, self.cn)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Partner.create({'name': 'Conflicting Province Fixture', 'country_id': self.env.ref('base.us').id,
                                 'state_id': self.sd.id})

    def test_contact_layout_follows_country_in_foreign_company(self):
        self.env.company.country_id = self.env.ref('base.us')
        view = self.Partner.get_view(self.env.ref('base.view_partner_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        main = arch.xpath("//sheet//div[@name='qing_cn_address']")[0]
        fields = [node.get('name') for node in main.xpath(".//field[not(@invisible='1')]")]
        ordered = ['country_id', 'state_id', 'city_id', 'qing_cn_district_id', 'street']
        self.assertEqual([name for name in fields if name in ordered], ordered)
        self.assertFalse(safe_eval(main.get('invisible'), {'country_code': 'CN'}))
        self.assertTrue(safe_eval(main.get('invisible'), {'country_code': 'US'}))
        foreign = main.getnext()
        self.assertTrue(safe_eval(foreign.get('invisible'), {'country_code': 'CN'}))
        self.assertFalse(safe_eval(foreign.get('invisible'), {'country_code': 'US'}))
        self.assertIn('city', [node.get('name') for node in foreign.xpath('.//field')])

    def test_china_company_keeps_native_foreign_fallback(self):
        self.env.company.country_id = self.cn
        view = self.Partner.get_view(self.env.ref('base.view_partner_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        for main in arch.xpath("//div[@name='qing_cn_address']"):
            foreign = main.getnext()
            self.assertFalse(safe_eval(foreign.get('invisible'), {'country_code': 'US'}))
            self.assertTrue(foreign.xpath(".//field[@name='street' and @placeholder='Street...']"))
        child = arch.xpath("//field[@name='child_ids']//form//div[@name='qing_cn_address']")
        self.assertEqual(len(child), 1)
        self.assertTrue(child[0].xpath(".//field[@name='qing_cn_district_id']"))

    def test_no_address_format_context_preserves_original_arch(self):
        view = self.Partner.with_context(no_address_format=True).get_view(
            self.env.ref('base.view_partner_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        self.assertFalse(arch.xpath("//div[@name='qing_cn_address']"))

    def test_form_country_switch_preserves_street_and_postcode(self):
        with strict_computes(), Form(self.partner, view=self.env.ref('base.view_partner_form')) as form:
            form.country_id = self.env.ref('base.us')
            form.city = 'Seattle'
            form.zip = '98101'
        self.assertFalse(self.partner.qing_cn_district_id)
        self.assertEqual(self.partner.street, 'Test Road 88')
        with strict_computes(), Form(self.partner, view=self.env.ref('base.view_partner_form')) as form:
            form.country_id = self.cn
            form.state_id = self.sd
            form.city_id = self.qd
            form.qing_cn_district_id = self.shibei
            form.zip = '266000'
        self.assertEqual(self.partner.city, '青岛市')
        self.assertEqual(self.partner.qing_cn_district_id, self.shibei)
        self.assertEqual(self.partner.street, 'Test Road 88')
        self.assertEqual(self.partner.zip, '266000')
