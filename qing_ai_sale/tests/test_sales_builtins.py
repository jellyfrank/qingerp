import importlib.util
import json
import re
from pathlib import Path
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiSalesBuiltins(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        manager_groups = 'base.group_user,sales_team.group_sale_salesman,base.group_partner_manager'
        editor_groups = manager_groups
        if cls.env.ref('qing_partner.group_partner_manager', raise_if_not_found=False):
            manager_groups += ',qing_partner.group_partner_manager'
            editor_groups += ',qing_partner.group_partner_editor'
        cls.manager = new_test_user(cls.env, login='qing-ai-builtin-manager', groups=manager_groups)
        cls.editor = new_test_user(cls.env, login='qing-ai-builtin-editor', groups=editor_groups)
        cls.company = cls.env.company
        cls.other_company = cls.env['res.company'].create({'name': 'AI Builtin Other Company'})
        for user in (cls.manager, cls.editor):
            user.write({'company_ids': [Command.link(cls.other_company.id)]})
        cls.has_customer_roles = 'qing_is_customer' in cls.env['res.partner']._fields
        customer_values = {
            'name': 'AI Builtin Customer Main', 'company_id': cls.company.id,
            'phone': '18500001111', 'email': 'original-builtin@example.invalid',
        }
        customer_values['qing_is_customer' if cls.has_customer_roles else 'customer_rank'] = True if cls.has_customer_roles else 1
        cls.customer = cls.env['res.partner'].create(customer_values)
        foreign_values = {
            'name': 'AI Builtin Customer Foreign', 'company_id': cls.other_company.id,
        }
        foreign_values['qing_is_customer' if cls.has_customer_roles else 'customer_rank'] = True if cls.has_customer_roles else 1
        cls.foreign_customer = cls.env['res.partner'].with_company(cls.other_company).create(foreign_values)
        cls.first_product = cls.env['product.product'].create({
            'name': 'AI Builtin First Product', 'default_code': 'AI-BUILTIN-FIRST',
            'list_price': 20.0, 'sale_ok': True,
        })
        cls.second_product = cls.env['product.product'].create({
            'name': 'AI Builtin Second Product', 'default_code': 'AI-BUILTIN-SECOND',
            'list_price': 40.0, 'sale_ok': True,
        })

    def _conversation(self, user=None):
        return self.env['qing.ai.conversation'].with_user(user or self.manager).with_context(
            allowed_company_ids=[self.company.id, self.other_company.id], lang='en_US',
        ).create({'agent_id': self.env.ref('qing_ai_sale.agent_sales').id})

    def _send_plan(self, conversation, key, arguments):
        result = {'text': json.dumps({'tool': key, 'arguments': arguments}),
                  'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result):
            return conversation.assistant_send('Please handle this sales request', task='business')

    def _pending_id(self, reply):
        self.assertEqual(len(reply['pending_operations']), 1, reply['messages'])
        return reply['pending_operations'][0]['id']

    def _query_count(self, reply):
        content = reply['messages'][-1]['content']
        match = re.search(r'Matching records in the current company: (\d+)\.', content)
        self.assertIsNotNone(match, content)
        return int(match.group(1))

    def _create_customer(self, name):
        values = {'name': name, 'company_id': self.company.id}
        values['qing_is_customer' if self.has_customer_roles else 'customer_rank'] = True if self.has_customer_roles else 1
        return self.env['res.partner'].create(values)

    def test_customer_creation_creates_a_business_customer(self):
        conversation = self._conversation()
        reply = self._send_plan(conversation, 'create_customer', {
            'name': 'AI Builtin Created Customer', 'phone': '18500002222',
        })
        operation_id = self._pending_id(reply)
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'AI Builtin Created Customer')]))
        conversation.assistant_confirm_operation(operation_id)
        customer = self.env['res.partner'].search([('name', '=', 'AI Builtin Created Customer')])
        self.assertEqual(len(customer), 1)
        if self.has_customer_roles:
            self.assertTrue(customer.qing_is_customer)
        else:
            self.assertEqual(customer.customer_rank, 1)
        query = self._send_plan(conversation, 'find_customers', {'search': customer.name})
        self.assertEqual(self._query_count(query), 1)
        self.assertIn(customer.name, query['messages'][-1]['content'])

    def test_customer_list_count_excludes_unclassified_and_other_company(self):
        conversation = self._conversation()
        before = self._query_count(self._send_plan(conversation, 'find_customers', {}))
        self._create_customer('AI Builtin Count One')
        self._create_customer('AI Builtin Count Two')
        self.env['res.partner'].create({'name': 'AI Builtin Count Contact', 'company_id': self.company.id})
        reply = self._send_plan(conversation, 'find_customers', {})
        self.assertEqual(self._query_count(reply), before + 2)
        self.assertNotIn(self.foreign_customer.name, reply['messages'][-1]['content'])
        filtered = self._send_plan(conversation, 'find_customers', {'search': 'AI Builtin Count'})
        self.assertEqual(self._query_count(filtered), 2)
        self.assertNotIn('AI Builtin Count Contact', filtered['messages'][-1]['content'])
        foreign = self._send_plan(conversation, 'find_customers', {'search': self.foreign_customer.name})
        self.assertEqual(self._query_count(foreign), 0)

    def test_customer_update_is_previewed_and_applied_once(self):
        conversation = self._conversation()
        old_name, old_phone = self.customer.name, self.customer.phone
        reply = self._send_plan(conversation, 'update_customer', {
            'customer_id': self.customer.id,
            'values': {'name': 'AI Builtin Customer Renamed', 'phone': '18500003333', 'email': ''},
        })
        operation_id = self._pending_id(reply)
        self.assertEqual(self.customer.name, old_name)
        self.assertEqual(self.customer.phone, old_phone)
        self.assertIn('18500003333', reply['pending_operations'][0]['preview'])
        conversation.assistant_confirm_operation(operation_id)
        self.assertEqual(self.customer.name, 'AI Builtin Customer Renamed')
        self.assertEqual(self.customer.phone, '18500003333')
        self.assertFalse(self.customer.email)
        self.customer.write({'phone': '18500004444'})
        conversation.assistant_confirm_operation(operation_id)
        self.assertEqual(self.customer.phone, '18500004444', 'A completed operation must not apply again.')

    def test_customer_update_by_name(self):
        conversation = self._conversation()
        reply = self._send_plan(conversation, 'update_customer', {
            'customer': self.customer.name, 'values': {'website': 'https://example.invalid'},
        })
        conversation.assistant_confirm_operation(self._pending_id(reply))
        self.assertEqual(self.customer.website, 'https://example.invalid')

    def test_customer_update_rejects_unapproved_fields_and_foreign_record(self):
        conversation = self._conversation()
        for values in ({'user_id': self.manager.id}, {'state': 'enabled'}, {'qing_is_customer': True}):
            with self.subTest(values=values), self.assertRaises(UserError):
                conversation._prepare_customer_update({'customer_id': self.customer.id, 'values': values})
        with self.assertRaises(UserError):
            conversation._prepare_customer_update({
                'customer_id': self.foreign_customer.id, 'values': {'phone': '18500005555'},
            })
        self.assertFalse(conversation._pending_business_operations())

    def test_customer_change_after_preview_blocks_update(self):
        conversation = self._conversation()
        reply = self._send_plan(conversation, 'update_customer', {
            'customer_id': self.customer.id, 'values': {'phone': '18500006666'},
        })
        self.customer.write({'name': 'AI Builtin Customer Changed Elsewhere'})
        with self.assertRaises(UserError):
            conversation.assistant_confirm_operation(self._pending_id(reply))
        self.assertEqual(self.customer.phone, '18500001111')

    def test_customer_update_rechecks_skill_authorization(self):
        conversation = self._conversation()
        reply = self._send_plan(conversation, 'update_customer', {
            'customer_id': self.customer.id, 'values': {'phone': '18500006666'},
        })
        tool = self.env.ref('qing_ai_sale.agent_tool_update_customer')
        tool.active = False
        with self.assertRaises(AccessError):
            conversation.assistant_confirm_operation(self._pending_id(reply))
        self.assertEqual(self.customer.phone, '18500001111')

    def test_partner_editor_can_update_contact_details_but_not_identity(self):
        if not self.has_customer_roles:
            self.skipTest('Requires qing_partner maintenance roles.')
        conversation = self._conversation(self.editor)
        reply = self._send_plan(conversation, 'update_customer', {
            'customer_id': self.customer.id, 'values': {'phone': '18500007777'},
        })
        conversation.assistant_confirm_operation(self._pending_id(reply))
        self.assertEqual(self.customer.phone, '18500007777')
        with self.assertRaises(AccessError):
            conversation._prepare_customer_update({
                'customer_id': self.customer.id, 'values': {'name': 'Unauthorized Rename'},
            })
        with self.assertRaises(AccessError):
            conversation._prepare_customer({'name': 'Unauthorized Customer Identity'})

    def test_partner_maintenance_revoked_after_preview_blocks_update(self):
        if not self.has_customer_roles:
            self.skipTest('Requires qing_partner maintenance roles.')
        conversation = self._conversation(self.editor)
        reply = self._send_plan(conversation, 'update_customer', {
            'customer_id': self.customer.id, 'values': {'phone': '18500008888'},
        })
        self.editor.write({'group_ids': [Command.unlink(self.env.ref('qing_partner.group_partner_editor').id)]})
        with self.assertRaises(AccessError):
            conversation.assistant_confirm_operation(self._pending_id(reply))
        self.assertEqual(self.customer.phone, '18500001111')

    def test_sales_order_draft_preserves_reversed_and_repeated_product_lines(self):
        conversation = self._conversation()
        reply = self._send_plan(conversation, 'create_quote_draft', {
            'customer': self.customer.name,
            'lines': [
                {'product': self.second_product.default_code, 'quantity': 2},
                {'product': self.first_product.default_code, 'quantity': 3},
                {'product': self.second_product.default_code, 'quantity': 4},
            ],
        })
        operation_id = self._pending_id(reply)
        preview = reply['pending_operations'][0]['preview']
        self.assertIn(f'{self.second_product.name} × 2', preview)
        self.assertIn(f'{self.second_product.name} × 4', preview)
        conversation.assistant_confirm_operation(operation_id)
        operation = self.env['qing.ai.sale.operation'].browse(operation_id)
        order = self.env['sale.order'].browse(operation.result_id)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.company_id, self.company)
        self.assertEqual([(line.product_id.id, line.product_uom_qty) for line in order.order_line], [
            (self.second_product.id, 2), (self.first_product.id, 3), (self.second_product.id, 4),
        ])
        conversation.assistant_confirm_operation(operation_id)
        self.assertEqual(len(order.order_line), 3)

    def test_sales_order_query_filters_state_and_current_company(self):
        current = self.env['sale.order'].create({
            'partner_id': self.customer.id, 'company_id': self.company.id,
            'user_id': self.manager.id,
        })
        foreign = self.env['sale.order'].with_company(self.other_company).create({
            'partner_id': self.foreign_customer.id, 'company_id': self.other_company.id,
            'user_id': self.manager.id,
        })
        conversation = self._conversation()
        reply = self._send_plan(conversation, 'find_quotes', {'search': current.name, 'state': 'draft'})
        self.assertEqual(self._query_count(reply), 1)
        self.assertIn(current.name, reply['messages'][-1]['content'])
        self.assertEqual(self._query_count(self._send_plan(conversation, 'find_quotes', {
            'search': current.name, 'state': 'sale',
        })), 0)
        self.assertEqual(self._query_count(self._send_plan(conversation, 'find_quotes', {
            'search': foreign.partner_id.name,
        })), 0)
        self.assertGreaterEqual(self._query_count(self._send_plan(conversation, 'find_quotes', {})), 1)

    def _migration_module(self):
        path = Path(__file__).resolve().parents[1] / 'migrations' / '20.0.2.0.4' / 'post-migrate.py'
        spec = importlib.util.spec_from_file_location('qing_ai_sale_builtins_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _legacy_stock_skill(self, migration):
        skill = self.env.ref('qing_ai_sale.skill_sales')
        tools = self.env['qing.ai.tool'].browse([
            self.env.ref(f'qing_ai_sale.agent_tool_{key}').id for key in migration.LEGACY_KEYS
        ])
        tools.write({'active': True, 'review_state': 'published', 'group_ids': [Command.clear()]})
        skill.write({
            'active': True, 'instructions': migration.LEGACY_INSTRUCTIONS,
            'group_ids': [Command.clear()], 'tool_ids': [Command.set(tools.ids)],
        })
        return skill

    def test_migration_expands_only_stock_sales_skill(self):
        migration = self._migration_module()
        skill = self._legacy_stock_skill(migration)
        migration.migrate(self.env.cr, '20.0.2.0.3')
        self.assertIn(self.env.ref('qing_ai_sale.agent_tool_update_customer'), skill.tool_ids)
        self.assertEqual(skill.instructions, migration.SALES_INSTRUCTIONS)

    def test_migration_preserves_customized_sales_skill_subset(self):
        migration = self._migration_module()
        skill = self._legacy_stock_skill(migration)
        skill.tool_ids = self.env.ref('qing_ai_sale.agent_tool_find_customers')
        migration.migrate(self.env.cr, '20.0.2.0.3')
        self.assertEqual(skill.tool_ids, self.env.ref('qing_ai_sale.agent_tool_find_customers'))

    def test_migration_preserves_disabled_or_group_restricted_sales_skill(self):
        migration = self._migration_module()
        skill = self._legacy_stock_skill(migration)
        skill.active = False
        migration.migrate(self.env.cr, '20.0.2.0.3')
        self.assertFalse(skill.active)
        self.assertNotIn(self.env.ref('qing_ai_sale.agent_tool_update_customer'), skill.tool_ids)
        skill.write({'active': True, 'group_ids': [Command.link(self.env.ref('base.group_system').id)]})
        migration.migrate(self.env.cr, '20.0.2.0.3')
        self.assertEqual(skill.group_ids, self.env.ref('base.group_system'))
        self.assertNotIn(self.env.ref('qing_ai_sale.agent_tool_update_customer'), skill.tool_ids)
