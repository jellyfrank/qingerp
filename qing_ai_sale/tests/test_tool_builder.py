import importlib.util
from pathlib import Path

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQingAiSalesToolBuilder(TransactionCase):
    def _runtime_migration(self):
        path = Path(__file__).resolve().parents[1] / 'migrations' / '20.0.3.0.0' / 'post-migrate.py'
        spec = importlib.util.spec_from_file_location('qing_ai_sale_runtime_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_presets_reuse_existing_adapters_and_skill_links(self):
        skill = self.env['qing.ai.skill'].create({'name': 'My Sales Skill'})
        for template, keys in [
            ('customers', {
                'orm_res_partner_search', 'orm_res_partner_read', 'orm_res_partner_count',
                'orm_res_partner_group', 'find_customers', 'create_customer', 'update_customer',
            }),
            ('sales_orders', {
                'orm_res_partner_search', 'orm_product_product_search',
                'orm_product_product_read', 'orm_product_product_count',
                'orm_product_product_group', 'orm_sale_order_search', 'orm_sale_order_read',
                'orm_sale_order_count', 'orm_sale_order_group', 'find_customers',
                'find_products', 'find_quotes', 'create_quote_draft',
            }),
        ]:
            wizard = self.env['qing.ai.tool.builder'].create({'template': template, 'skill_id': skill.id})
            first_ids = set()
            for _repeat in range(2):
                action = wizard.action_generate()
                tools = self.env['qing.ai.tool'].search(action['domain'])
                self.assertEqual(set(tools.mapped('key')), keys)
                if first_ids:
                    self.assertEqual(set(tools.ids), first_ids)
                else:
                    first_ids = set(tools.ids)
                self.assertTrue(all(tool in skill.tool_ids for tool in tools))
        self.assertEqual(len(skill.tool_ids), 18)

    def test_runtime_migration_expands_untouched_stock_skills(self):
        migration = self._runtime_migration()
        for skill_xmlid, config in migration.SKILLS.items():
            skill = self.env.ref('qing_ai_sale.' + skill_xmlid)
            legacy = self.env['qing.ai.tool'].browse([
                self.env.ref('qing_ai_sale.agent_tool_' + key).id
                for key in config['legacy']
            ])
            skill.tool_ids = [Command.set(legacy.ids)]
            migration.migrate(self.env.cr, '20.0.2.0.4')
            expected = {
                self.env.ref('qing_ai_sale.' + xmlid).key
                for xmlid in config['tools']
            }
            self.assertEqual(set(skill.tool_ids.mapped('key')), expected)

    def test_runtime_migration_preserves_customized_skill_subset(self):
        migration = self._runtime_migration()
        skill = self.env.ref('qing_ai_sale.skill_customers')
        tool = self.env.ref('qing_ai_sale.agent_tool_find_customers')
        skill.tool_ids = tool
        migration.migrate(self.env.cr, '20.0.2.0.4')
        self.assertEqual(skill.tool_ids, tool)

    def test_mixed_catalog_plans_generic_and_sales_tools(self):
        model = self.env['ir.model']._get('res.partner.category')
        tool = self.env['qing.ai.tool'].create({
            'name': 'Count Tags', 'execution_type': 'orm', 'model_id': model.id, 'orm_operation': 'count',
        })
        skill = self.env.ref('qing_ai_sale.skill_sales')
        skill.tool_ids = [Command.link(tool.id)]
        conversation = self.env['qing.ai.conversation'].create({'agent_id': self.env.ref('qing_ai_sale.agent_sales').id})
        catalog = conversation._business_tool_descriptions()
        self.assertIn(tool.key, catalog)
        self.assertIn('create_customer', catalog)
        self.assertIn('count', conversation._dispatch_business_plan(tool.key, {}))
        conversation._dispatch_business_plan('create_customer', {'name': 'Mixed Catalog Customer'})
        generic = self.env['qing.ai.tool'].create({
            'name': 'Create Tag', 'execution_type': 'orm', 'model_id': model.id, 'orm_operation': 'create',
            'orm_write_field_ids': [Command.set(model.field_id.filtered(lambda f: f.name == 'name').ids)],
        })
        skill.tool_ids = [Command.link(generic.id)]
        conversation._dispatch_business_plan(generic.key, {'values': {'name': 'Mixed Catalog Tag'}})
        pending = conversation._pending_business_operations()
        self.assertEqual(len(pending), 2)
        self.assertEqual(sum(isinstance(item['id'], str) for item in pending), 1)
        for item in pending:
            conversation.assistant_confirm_operation(item['id'])
        self.assertTrue(self.env['res.partner'].search([('name', '=', 'Mixed Catalog Customer')]))
        self.assertTrue(self.env['res.partner.category'].search([('name', '=', 'Mixed Catalog Tag')]))
