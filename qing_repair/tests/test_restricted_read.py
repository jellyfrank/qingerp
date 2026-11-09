from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestRepairRestrictedRead(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = new_test_user(
            cls.env, login='qing_repair_partial_reader',
            groups='base.group_user,base.group_partner_manager,stock.group_stock_user,sales_team.group_sale_salesman',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.customer = cls.env['res.partner'].create({'name': 'Repair permission customer'})
        cls.quotation = cls.env['sale.order'].create({
            'partner_id': cls.customer.id, 'user_id': cls.env.ref('base.user_admin').id,
        })
        cls.env['ir.access'].create({
            'name': 'Repair test own sales orders',
            'model_id': cls.env['ir.model']._get('sale.order').id,
            'operation': 'r',
            'domain': "[('user_id', '=', user.id)]",
        })
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.repairs = cls.env['repair.order'].create([
            {
                'partner_id': cls.customer.id, 'picking_type_id': cls.warehouse.repair_type_id.id,
                'qing_pricing_mode': 'direct',
            },
            {
                'partner_id': cls.customer.id, 'picking_type_id': cls.warehouse.repair_type_id.id,
                'qing_pricing_mode': 'sale', 'sale_order_id': cls.quotation.id,
            },
        ])

    def test_real_repair_list_and_form_survive_unreadable_quotation(self):
        specification = {
            'name': {}, 'state': {}, 'qing_pricing_mode': {}, 'qing_currency_id': {},
            'qing_amount_total': {}, 'qing_amount_tax': {}, 'qing_amount_untaxed': {},
        }
        self.env.invalidate_all()
        repairs = self.repairs.with_user(self.operator).with_context(allowed_company_ids=self.env.company.ids)
        with self.assertRaises(AccessError):
            self.quotation.with_env(repairs.env).read(['currency_id'])
        expected = ['qing_amount_tax', 'qing_amount_total', 'qing_amount_untaxed', 'qing_currency_id']
        for values in (
            repairs.web_read(specification),
            repairs.web_search_read([('id', 'in', repairs.ids)], specification, order='id')['records'],
        ):
            self.assertEqual(len(values), 2)
            self.assertEqual(values[0]['__qing_restricted_fields'], [])
            self.assertEqual(values[0]['qing_currency_id'], self.env.company.currency_id.id)
            self.assertEqual(values[0]['qing_amount_total'], 0)
            self.assertEqual(values[1]['__qing_restricted_fields'], expected)
            self.assertEqual(values[1]['state'], 'draft')
            for name in expected:
                self.assertNotIn(name, values[1])
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            repairs[1].read(['qing_currency_id'])

    def test_native_relation_display_and_authorized_amounts_still_work(self):
        self.env.invalidate_all()
        repair = self.repairs[1].with_user(self.operator)
        value = repair.web_read({
            'name': {}, 'sale_order_id': {'fields': {'display_name': {}}},
        })[0]
        self.assertEqual(value['__qing_restricted_fields'], [])
        self.assertEqual(value['sale_order_id']['id'], self.quotation.id)
        self.env.invalidate_all()
        value = self.repairs[1].web_read({'qing_currency_id': {}, 'qing_amount_total': {}})[0]
        self.assertEqual(value['__qing_restricted_fields'], [])
        self.assertEqual(value['qing_currency_id'], self.env.company.currency_id.id)
        self.assertEqual(value['qing_amount_total'], 0)
