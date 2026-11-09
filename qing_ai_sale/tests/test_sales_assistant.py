import json
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiSalesAssistant(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = 'base.group_user,sales_team.group_sale_salesman'
        if cls.env.ref('qing_partner.group_partner_manager', raise_if_not_found=False):
            groups += ',qing_partner.group_partner_manager'
        cls.user = new_test_user(
            cls.env, login='qing-ai-sales-user',
            groups=groups,
        )
        cls.other = new_test_user(cls.env, login='qing-ai-sales-other', groups='base.group_user')
        customer_values = {'name': 'AI Sales Customer Unique'}
        role_field = 'qing_is_customer' if 'qing_is_customer' in cls.env['res.partner']._fields else 'customer_rank'
        customer_values[role_field] = 1
        cls.customer = cls.env['res.partner'].create(customer_values)
        cls.product = cls.env['product.product'].create({
            'name': 'AI Sales Product Unique', 'list_price': 37.0, 'sale_ok': True,
        })

    def _conversation(self):
        return self.env['qing.ai.conversation'].with_user(self.user).create({})

    def _send_plan(self, conversation, key, arguments):
        result = {
            'text': json.dumps({'tool': key, 'arguments': arguments}),
            'model': 'test-model', 'usage': {},
        }
        with patch(
            'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
            return_value=result,
        ):
            return conversation.assistant_send('Please handle this sales request', task='business')

    def test_customer_query_uses_current_users_record_access(self):
        conversation = self._conversation()
        result = self._send_plan(conversation, 'find_customers', {'search': 'AI Sales Customer'})
        self.assertIn(self.customer.name, result['messages'][-1]['content'])
        self.assertTrue(result['business_enabled'])
        self.assertFalse(result['pending_operations'])

    def test_generic_customer_query_uses_company_role_and_related_state(self):
        if 'qing_is_customer' not in self.env['res.partner']._fields:
            self.skipTest('Requires qing_partner company-specific customer roles.')
        state = self.env['res.country.state'].create({
            'name': 'AI Shandong Province', 'code': 'AISD',
            'country_id': self.env.ref('base.cn').id,
        })
        target = self.env['res.partner'].create({
            'name': 'AI Shandong Customer', 'country_id': self.env.ref('base.cn').id,
            'state_id': state.id, 'qing_is_customer': True,
        })
        ranked_non_customer = self.env['res.partner'].create({
            'name': 'AI Shandong Ranked Contact', 'country_id': self.env.ref('base.cn').id,
            'state_id': state.id, 'customer_rank': 50,
        })
        tool = self.env.ref('qing_ai_sale.tool_partner_search').with_user(self.user)
        result = tool._orm_query({
            'domain': [['state_id.name', 'ilike', 'Shandong']],
            'fields': ['name', 'state_id'],
        })
        self.assertIn(target.name, result)
        self.assertNotIn(ranked_non_customer.name, result)

    def test_customer_creation_requires_confirmation_and_is_idempotent(self):
        conversation = self._conversation()
        count_before = self.env['res.partner'].search_count([('name', '=', 'AI New Customer')])
        result = self._send_plan(conversation, 'create_customer', {
            'name': 'AI New Customer', 'phone': '18512341234',
        })
        self.assertTrue(result['pending_operations'], result['messages'])
        operation_id = result['pending_operations'][0]['id']
        self.assertEqual(self.env['res.partner'].search_count([('name', '=', 'AI New Customer')]), count_before)
        conversation.assistant_confirm_operation(operation_id)
        customer = self.env['res.partner'].search([('name', '=', 'AI New Customer')], limit=1)
        self.assertEqual(customer.phone, '18512341234')
        self.assertEqual(customer.company_id, self.env.company)
        conversation.assistant_confirm_operation(operation_id)
        self.assertEqual(self.env['res.partner'].search_count([('name', '=', 'AI New Customer')]), count_before + 1)

    def test_text_confirmation_executes_the_saved_customer_preview(self):
        conversation = self._conversation()
        self._send_plan(conversation, 'create_customer', {'name': 'AI Text Confirm Customer'})
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text') as complete:
            reply = conversation.assistant_send('确认')
        complete.assert_not_called()
        self.assertFalse(reply['pending_operations'])
        self.assertTrue(self.env['res.partner'].search([('name', '=', 'AI Text Confirm Customer')]))

    def test_failed_confirmation_rolls_back_partial_business_write(self):
        conversation = self._conversation()
        self._send_plan(conversation, 'create_customer', {'name': 'AI Rolled Back Customer'})

        def fail_after_create(record, arguments):
            record.env['res.partner'].create({'name': arguments['name']})
            raise UserError('Synthetic failure after business write')

        with patch.object(type(conversation), '_confirm_customer', fail_after_create):
            reply = conversation.assistant_send('确认')
        self.assertEqual(reply['messages'][-1]['role'], 'error')
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'AI Rolled Back Customer')]))
        self.assertEqual(len(reply['pending_operations']), 1)

    def test_confirmation_without_a_saved_preview_never_calls_the_provider(self):
        conversation = self._conversation()
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text') as complete:
            reply = conversation.assistant_send('确认')
        complete.assert_not_called()
        self.assertEqual(reply['messages'][-1]['role'], 'error')
        self.assertIn('No business action is awaiting confirmation',
                      reply['messages'][-1]['content'])
        self.assertFalse(reply['pending_operations'])

    def test_translation_of_confirmation_word_is_not_a_business_confirmation(self):
        conversation = self._conversation()
        result = {'text': 'Confirm', 'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result) as complete:
            reply = conversation.assistant_send('确认', task='translate')
        complete.assert_called_once()
        self.assertEqual(reply['messages'][-1]['content'], 'Confirm')

    def test_text_confirmation_requires_one_pending_preview(self):
        conversation = self._conversation()
        self._send_plan(conversation, 'create_customer', {'name': 'AI Pending One'})
        self._send_plan(conversation, 'create_customer', {'name': 'AI Pending Two'})
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text') as complete:
            reply = conversation.assistant_send('确认')
        complete.assert_not_called()
        self.assertEqual(reply['messages'][-1]['role'], 'error')
        self.assertFalse(self.env['res.partner'].search([('name', 'in', ['AI Pending One', 'AI Pending Two'])]))

    def test_customer_requires_name_and_rejects_visible_duplicate(self):
        conversation = self._conversation()
        with self.assertRaises(UserError):
            conversation._prepare_customer({'phone': '18512341234'})
        with self.assertRaises(UserError):
            conversation._prepare_customer({'name': self.customer.name})

    def test_normal_ask_can_plan_customer_creation(self):
        conversation = self._conversation()
        result = {'text': json.dumps({'tool': 'create_customer', 'arguments': {
            'name': 'AI Normal Ask Customer', 'phone': '18500001111',
        }}), 'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result):
            reply = conversation.assistant_send('Create customer AI Normal Ask Customer, 18500001111')
        self.assertEqual(len(reply['pending_operations']), 1, reply['messages'])
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'AI Normal Ask Customer')]))

    def test_business_prompt_names_action_keys_and_excludes_ask_only_policy(self):
        conversation = self._conversation()
        result = {'text': json.dumps({'tool': 'create_customer', 'arguments': {
            'name': 'AI Prompt Customer',
        }}), 'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result) as complete:
            reply = conversation.assistant_send('请创建客户 AI Prompt Customer')
        system = complete.call_args.kwargs['system']
        self.assertIn('create_customer: Preview a new customer', system)
        self.assertNotIn('Do not execute business actions', system)
        self.assertTrue(complete.call_args.kwargs['json_mode'])
        self.assertIn('请创建客户 AI Prompt Customer', complete.call_args.args[0])
        self.assertEqual(len(reply['pending_operations']), 1)

    def test_ask_preview_then_text_confirmation_creates_customer_once(self):
        conversation = self._conversation()
        result = {'text': json.dumps({'tool': 'create_customer', 'arguments': {
            'name': 'AI Confirmed Ask Customer', 'phone': '18500001234',
        }}), 'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result):
            preview = conversation.assistant_send('请创建客户 AI Confirmed Ask Customer，电话18500001234')
        self.assertEqual(len(preview['pending_operations']), 1)
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'AI Confirmed Ask Customer')]))
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text') as complete:
            confirmed = conversation.assistant_send('确认')
        complete.assert_not_called()
        self.assertFalse(confirmed['pending_operations'])
        customers = self.env['res.partner'].search([('name', '=', 'AI Confirmed Ask Customer')])
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers.phone, '18500001234')

    def test_invalid_action_key_is_retried_before_staging_a_customer(self):
        conversation = self._conversation()
        responses = [
            {'text': json.dumps({'tool': 'create_record', 'arguments': {
                'name': 'AI Retried Customer',
            }}), 'model': 'test-model', 'usage': {}},
            {'text': json.dumps({'tool': 'create_customer', 'arguments': {
                'name': 'AI Retried Customer',
            }}), 'model': 'test-model', 'usage': {}},
        ]
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   side_effect=responses) as complete:
            reply = conversation.assistant_send('请创建客户 AI Retried Customer')
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(len(reply['pending_operations']), 1)
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'AI Retried Customer')]))

    def test_plain_text_claim_cannot_masquerade_as_a_saved_preview(self):
        conversation = self._conversation()
        result = {'text': 'I have previewed the customer. Please confirm.',
                  'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result) as complete:
            reply = conversation.assistant_send('请创建客户 AI Unstaged Customer')
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(reply['messages'][-1]['role'], 'error')
        self.assertIn('No record was staged', reply['messages'][-1]['content'])
        self.assertFalse(reply['pending_operations'])

    def test_answer_to_write_request_does_not_claim_a_preview(self):
        conversation = self._conversation()
        result = {'text': json.dumps({'answer': 'I previewed it; please confirm.'}),
                  'model': 'test-model', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result):
            reply = conversation.assistant_send('请创建客户 AI Answer Customer')
        self.assertIn('No business action was staged', reply['messages'][-1]['content'])
        self.assertFalse(reply['pending_operations'])

    def test_normal_ask_can_answer_without_forcing_a_tool(self):
        conversation = self._conversation()
        result = {'text': 'Hello there.', 'model': 'test', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result) as complete:
            reply = conversation.assistant_send('Hello')
        self.assertEqual(reply['messages'][-1]['content'], 'Hello there.')
        # Small talk is answered without sending the ERP tool catalog.
        complete.assert_called_once()
        self.assertNotIn('Allowed tools', complete.call_args.kwargs['system'])

    def test_missing_order_details_are_requested_before_preview(self):
        conversation = self._conversation()
        result = {'text': json.dumps({'question': 'Which customer, products and quantities?'}),
                  'model': 'test', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   return_value=result):
            reply = conversation.assistant_send('创建SO')
        self.assertEqual(reply['messages'][-1]['content'], 'Which customer, products and quantities?')
        self.assertFalse(reply['pending_operations'])

    def test_unbounded_or_nonfinite_quantity_is_rejected(self):
        conversation = self._conversation()
        for quantity in (10 ** 400, float('nan'), float('inf'), True):
            with self.assertRaises(UserError):
                conversation._prepare_quote({'customer': self.customer.name, 'lines': [
                    {'product': self.product.name, 'quantity': quantity},
                ]})

    def test_unstructured_plain_answer_is_retried_as_an_answer_object(self):
        conversation = self._conversation()
        responses = [
            {'text': 'Hello there.', 'model': 'test', 'usage': {}},
            {'text': json.dumps({'answer': 'Hello there.'}), 'model': 'test', 'usage': {}},
        ]
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
                   side_effect=responses) as complete:
            reply = conversation.assistant_send('Hello', task='business')
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(reply['messages'][-1]['content'], 'Hello there.')

    def test_reviewed_discovered_customer_action_can_be_combined_into_skill(self):
        Tool = self.env['qing.ai.tool']
        adapter = self.env.ref('qing_ai_sale.agent_tool_create_customer')
        candidates = Tool.discover_module(self.env.ref('base.module_base').id,
                                          self.env.ref('base.model_res_partner').id)
        candidate = candidates.filtered(lambda tool: tool.discovery_kind == 'model_create')
        self.assertEqual(len(candidate), 1)
        candidate.adapter_tool_id = adapter
        candidate.action_publish()
        skill = self.env['qing.ai.skill'].create({
            'name': 'Contact Creation', 'tool_ids': [Command.link(candidate.id)],
        })
        agent = self.env['qing.ai.agent'].create({
            'name': 'Contact Agent', 'skill_ids': [Command.link(skill.id)],
        })
        conversation = self.env['qing.ai.conversation'].with_user(self.user).create({
            'agent_id': agent.id,
        })
        self.assertEqual(conversation._allowed_sale_tools(), {'create_customer'})
        adapter.active = False
        self.assertFalse(conversation._allowed_sale_tools())

    def test_candidate_cannot_publish_with_wrong_business_adapter(self):
        Tool = self.env['qing.ai.tool']
        candidate = Tool.create({
            'name': 'Create Contact', 'key': 'discovered:test-wrong-adapter',
            'review_state': 'needs_adapter',
            'source_module_id': self.env.ref('base.module_qing_ai_sale').id,
            'source_version': self.env.ref('base.module_qing_ai_sale').installed_version,
            'model_id': self.env.ref('base.model_res_partner').id,
            'discovery_kind': 'model_create',
            'adapter_tool_id': self.env.ref('qing_ai_sale.agent_tool_find_customers').id,
        })
        with self.assertRaises(UserError):
            candidate.action_publish()

    def test_source_change_revokes_published_candidate(self):
        Tool = self.env['qing.ai.tool']
        module = self.env.ref('base.module_qing_ai_sale')
        values = {
            'name': 'Create Contact', 'model_id': self.env.ref('base.model_res_partner').id,
            'discovery_kind': 'model_create',
        }
        candidate = Tool._upsert_candidate(module, 'model_create', 'res.partner.test',
                                           values, signature=['name'])
        candidate.adapter_tool_id = self.env.ref('qing_ai_sale.agent_tool_create_customer')
        candidate.action_publish()
        Tool._upsert_candidate(module, 'model_create', 'res.partner.test',
                               values, signature=['name', 'phone'])
        self.assertEqual(candidate.review_state, 'needs_review')
        self.assertFalse(candidate.available_for_user())

    def test_product_quotation_and_activity_queries(self):
        conversation = self._conversation()
        quotation = self.env['sale.order'].with_user(self.user).create({
            'partner_id': self.customer.id,
        })
        activity = self.env['mail.activity'].with_user(self.user).create({
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'date_deadline': fields.Date.context_today(conversation),
            'res_model_id': self.env['ir.model']._get('res.partner').id,
            'res_id': self.customer.id,
            'summary': 'AI query activity',
            'user_id': self.user.id,
        })
        for tool, arguments, expected in (
            ('find_products', {'search': 'AI Sales Product'}, self.product.name),
            ('find_quotes', {'search': quotation.name}, quotation.name),
            ('find_activities', {'customer': self.customer.name}, activity.summary),
        ):
            result = self._send_plan(conversation, tool, arguments)
            self.assertEqual(result['messages'][-1]['role'], 'assistant')
            self.assertIn(expected, result['messages'][-1]['content'])

    def test_tool_group_does_not_grant_native_sales_rights(self):
        conversation = self.env['qing.ai.conversation'].with_user(self.other).create({})
        self.assertNotIn('create_quote_draft', conversation._allowed_sale_tools())

    def test_agent_skill_narrows_sales_tools_and_rechecks_before_confirmation(self):
        tool = self.env.ref('qing_ai_sale.agent_tool_find_customers')
        skill = self.env['qing.ai.skill'].create({
            'name': 'Customer Lookup Only', 'tool_ids': [Command.link(tool.id)],
        })
        agent = self.env['qing.ai.agent'].create({
            'name': 'Customer Lookup Agent', 'skill_ids': [Command.link(skill.id)],
        })
        conversation = self.env['qing.ai.conversation'].with_user(self.user).create({
            'agent_id': agent.id,
        })
        self.assertEqual(conversation._allowed_sale_tools(), {'find_customers'})
        skill.tool_ids = [Command.clear()]
        self.assertFalse(conversation._allowed_sale_tools())
        self.assertFalse(conversation.assistant_open()['business_enabled'])

    def test_legacy_conversation_binds_sales_agent_before_business_request(self):
        conversation = self._conversation()
        conversation.write({'agent_id': False})
        reply = self._send_plan(conversation, 'find_customers', {'search': 'AI Sales Customer'})
        self.assertEqual(conversation.agent_id, self.env.ref('qing_ai_sale.agent_sales'))
        self.assertIn(self.customer.name, reply['messages'][-1]['content'])

    def test_quotation_requires_confirmation_and_is_idempotent(self):
        conversation = self._conversation()
        existing = self.env['sale.order'].search_count([('partner_id', '=', self.customer.id)])
        result = self._send_plan(conversation, 'create_quote_draft', {
            'customer': self.customer.name,
            'lines': [{'product': self.product.name, 'quantity': 2}],
        })
        operation_id = result['pending_operations'][0]['id']
        self.assertEqual(self.env['sale.order'].search_count([('partner_id', '=', self.customer.id)]), existing)
        confirmed = conversation.assistant_confirm_operation(operation_id)
        self.assertFalse(confirmed['pending_operations'])
        order = self.env['sale.order'].search([('partner_id', '=', self.customer.id)], order='id desc', limit=1)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.order_line.product_id, self.product)
        self.assertEqual(order.order_line.product_uom_qty, 2)
        self.assertEqual(order.order_line.price_unit, 37.0)
        conversation.assistant_confirm_operation(operation_id)
        self.assertEqual(self.env['sale.order'].search_count([('partner_id', '=', self.customer.id)]), existing + 1)

    def test_followup_requires_confirmation_and_is_assigned_to_requester(self):
        conversation = self._conversation()
        tomorrow = fields.Date.to_string(fields.Date.context_today(conversation) + timedelta(days=1))
        result = self._send_plan(conversation, 'create_followup', {
            'customer': self.customer.name,
            'summary': 'Call about renewed offer',
            'due_date': tomorrow,
            'note': 'Ask for quantities.',
        })
        operation_id = result['pending_operations'][0]['id']
        before = self.env['mail.activity'].search_count([
            ('res_model', '=', 'res.partner'), ('res_id', '=', self.customer.id),
        ])
        conversation.assistant_confirm_operation(operation_id)
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', self.customer.id),
        ])
        self.assertEqual(len(activities), before + 1)
        activity = activities.sorted('id')[-1]
        self.assertEqual(activity.user_id, self.user)
        self.assertEqual(activity.summary, 'Call about renewed offer')

    def test_legacy_sale_permission_does_not_override_skill(self):
        conversation = self._conversation()
        tool = self.env.ref('qing_ai_sale.tool_create_quote_draft')
        tool.write({'allowed_group_ids': [Command.clear()]})
        self.assertIn('create_quote_draft', conversation._allowed_sale_tools())

    def test_skill_revocation_blocks_pending_quotation(self):
        conversation = self._conversation()
        result = self._send_plan(conversation, 'create_quote_draft', {
            'customer': self.customer.name,
            'lines': [{'product': self.product.name, 'quantity': 1}],
        })
        operation_id = result['pending_operations'][0]['id']
        skill = self.env.ref('qing_ai_sale.skill_sales')
        tool = self.env.ref('qing_ai_sale.agent_tool_create_quote_draft')
        skill.tool_ids = [Command.unlink(tool.id)]
        self.assertNotIn('create_quote_draft', conversation._allowed_sale_tools())
        with self.assertRaises(AccessError):
            conversation.assistant_confirm_operation(operation_id)

    def test_recheck_changed_product(self):
        conversation = self._conversation()
        result = self._send_plan(conversation, 'create_quote_draft', {
            'customer': self.customer.name,
            'lines': [{'product': self.product.name, 'quantity': 1}],
        })
        operation_id = result['pending_operations'][0]['id']
        self.product.list_price = 40.0
        with self.assertRaises(UserError):
            conversation.assistant_confirm_operation(operation_id)

    def test_another_user_cannot_confirm_pending_operation(self):
        conversation = self._conversation()
        result = self._send_plan(conversation, 'create_quote_draft', {
            'customer': self.customer.name,
            'lines': [{'product': self.product.name, 'quantity': 1}],
        })
        with self.assertRaises(AccessError):
            conversation.with_user(self.other).assistant_confirm_operation(
                result['pending_operations'][0]['id']
            )

    def test_expired_sales_previews_are_vacuumed_but_results_are_kept(self):
        conversation = self._conversation()
        Operation = self.env['qing.ai.sale.operation'].sudo()
        values = {
            'arguments': {'name': 'Vacuum Customer'}, 'company_id': conversation.company_id.id,
            'conversation_id': conversation.id, 'fingerprint': 'x', 'owner_id': self.user.id,
            'preview': 'Preview', 'tool_key': 'create_customer',
        }
        old = fields.Datetime.now() - timedelta(days=3)
        stale = Operation.create(dict(values, expires_at=old))
        confirmed = Operation.create(dict(values, expires_at=old, state='done'))
        fresh = Operation.create(dict(values, expires_at=fields.Datetime.now() + timedelta(minutes=10)))
        done, remaining = Operation._gc_expired_previews()
        self.assertEqual((done, remaining), (1, False))
        self.assertFalse(stale.exists())
        self.assertTrue(confirmed.exists())
        self.assertTrue(fresh.exists())
