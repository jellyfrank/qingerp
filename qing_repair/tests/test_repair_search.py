from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestRepairTextSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = new_test_user(
            cls.env, login='qing_repair_text_search',
            groups='base.group_user,stock.group_stock_user',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.repairs = cls.env['repair.order'].create([
            {'picking_type_id': warehouse.repair_type_id.id, 'qing_device_model': '柯美286i',
             'qing_repair_content': '更换黄色硒鼓\n打印测试正常'},
            {'picking_type_id': warehouse.repair_type_id.id, 'qing_device_model': 'HP LaserJet 227',
             'qing_repair_content': '安装驱动调试'},
            {'picking_type_id': warehouse.repair_type_id.id},
        ])

    def _search_arch(self):
        Repair = self.env['repair.order'].with_user(self.operator)
        view = self.env.ref('repair.view_repair_order_form_filter')
        return etree.fromstring(Repair.get_view(view.id, 'search')['arch'])

    def _domain(self, field_name, keyword):
        [node] = self._search_arch().xpath(f"/search/field[@name='{field_name}']")
        return safe_eval(node.get('filter_domain'), {'self': keyword})

    def _search(self, domain):
        Repair = self.env['repair.order'].with_user(self.operator)
        return Repair.search([('id', 'in', self.repairs.ids), *domain])

    def test_two_text_search_items_are_added_once(self):
        arch = self._search_arch()
        for field_name in ('qing_device_model', 'qing_repair_content'):
            self.assertEqual(len(arch.xpath(f"/search/field[@name='{field_name}']")), 1)
            self.assertEqual(self._domain(field_name, 'keyword'), [(field_name, 'ilike', 'keyword')])

    def test_device_model_supports_partial_chinese_and_case_insensitive_text(self):
        for keyword in ('柯美', '286'):
            self.assertEqual(self._search(self._domain('qing_device_model', keyword)), self.repairs[0])
        self.assertEqual(self._search(self._domain('qing_device_model', 'laserjet')), self.repairs[1])

    def test_repair_content_supports_partial_and_multiline_text(self):
        for keyword in ('黄色', '打印测试'):
            self.assertEqual(self._search(self._domain('qing_repair_content', keyword)), self.repairs[0])
        self.assertEqual(self._search(self._domain('qing_repair_content', '驱动')), self.repairs[1])

    def test_search_fields_are_distinct_and_can_be_combined(self):
        self.assertFalse(self._search(self._domain('qing_device_model', '黄色')))
        self.assertFalse(self._search(self._domain('qing_repair_content', '286')))
        domain = self._domain('qing_device_model', '286') + self._domain('qing_repair_content', '黄色')
        self.assertEqual(self._search(domain), self.repairs[0])

    def test_native_search_fields_filters_and_groupings_are_retained(self):
        arch = self._search_arch()
        for name in ('name', 'product_id', 'partner_id', 'sale_order_id'):
            self.assertTrue(arch.xpath(f"/search/field[@name='{name}']"))
        [name] = arch.xpath("/search/field[@name='name']")
        self.assertEqual(safe_eval(name.get('filter_domain'), {'self': 'keyword'}),
                         ['|', ('name', 'ilike', 'keyword'), ('product_id', 'ilike', 'keyword')])
        for name in ('filter_draft', 'filter_confirmed', 'filter_done', 'filter_cancel',
                     'filter_create_date', 'partner', 'product', 'status'):
            self.assertTrue(arch.xpath(f"//filter[@name='{name}']"))

    def test_search_keeps_company_access_rules(self):
        company = self.env['res.company'].create({'name': 'Repair search other company'})
        Warehouse = self.env['stock.warehouse']
        warehouse = Warehouse.search([('company_id', '=', company.id)], limit=1)
        if not warehouse:
            warehouse = Warehouse.create({'name': 'Repair search warehouse', 'code': 'RST', 'company_id': company.id})
        hidden = self.env['repair.order'].with_company(company).create({
            'company_id': company.id, 'picking_type_id': warehouse.repair_type_id.id,
            'qing_device_model': '柯美286i', 'qing_repair_content': '更换黄色硒鼓',
        })
        Repair = self.env['repair.order'].with_user(self.operator).with_context(allowed_company_ids=self.env.company.ids)
        domain = [('id', 'in', (self.repairs | hidden).ids), *self._domain('qing_device_model', '286')]
        self.assertEqual(Repair.search(domain), self.repairs[0])
        with self.assertRaises(AccessError):
            hidden.with_env(Repair.env).read(['qing_device_model', 'qing_repair_content'])

    def test_search_uses_translated_field_labels_not_fixed_customer_strings(self):
        self.env['res.lang']._activate_lang('zh_CN')
        Module = self.env['ir.module.module']
        Module.search([('name', '=', 'qing_repair')])._update_translations(filter_lang='zh_CN', overwrite=True)
        Repair = self.env['repair.order'].with_user(self.operator).with_context(lang='zh_CN')
        fields = Repair.fields_get(['qing_device_model', 'qing_repair_content'])
        self.assertEqual(fields['qing_device_model']['string'], '设备型号')
        self.assertEqual(fields['qing_repair_content']['string'], '维修记录')
        english = Repair.with_context(lang='en_US').fields_get(['qing_repair_content'])
        self.assertEqual(english['qing_repair_content']['string'], Repair._fields['qing_repair_content'].string)
        view = self.env.ref('qing_repair.view_repair_order_search')
        arch = etree.fromstring(view.arch_db)
        self.assertFalse(arch.xpath("//field[@name='qing_device_model' or @name='qing_repair_content'][@string]"))

    def test_portal_and_public_users_do_not_gain_repair_read_access(self):
        public = self.env.ref('base.public_user')
        portal = new_test_user(self.env, login='qing_repair_search_portal', groups='base.group_portal')
        for user in (public, portal):
            self.assertFalse(self.env['repair.order'].with_user(user).has_access('read'))
