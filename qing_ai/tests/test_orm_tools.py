import json
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiOrmTools(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='ai-orm-user', groups='base.group_user,base.group_partner_manager')
        cls.other = new_test_user(cls.env, login='ai-orm-other', groups='base.group_user')
        cls.model = cls.env['ir.model']._get('res.partner.category')
        cls.read_fields = cls.model.field_id.filtered(lambda f: f.name in ('name', 'color'))
        cls.skill = cls.env['qing.ai.skill'].create({'name': 'Manage Tags'})
        cls.agent = cls.env['qing.ai.agent'].create({
            'name': 'Tag Assistant', 'skill_ids': [Command.link(cls.skill.id)],
        })
        cls.tag = cls.env['res.partner.category'].create({'name': 'AI ORM Existing', 'color': 1})

    def _tool(self, operation, **extra):
        values = {
            'name': 'Tags ' + operation, 'execution_type': 'orm', 'model_id': self.model.id,
            'orm_operation': operation, 'orm_field_ids': [Command.set(self.read_fields.ids)],
            'orm_write_field_ids': [Command.set(self.read_fields.ids)] if operation in ('create', 'write') else [],
        }
        values.update(extra)
        tool = self.env['qing.ai.tool'].create(values)
        self.skill.tool_ids = [Command.link(tool.id)]
        return tool.with_user(self.user)

    def _conversation(self):
        return self.env['qing.ai.conversation'].with_user(self.user).create({'agent_id': self.agent.id})

    def _send(self, conversation, tool, arguments):
        result = {'text': json.dumps({'tool': tool.key, 'arguments': arguments}), 'model': 'mock', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result):
            # Explicit business task: these tests exercise the planner, not Ask routing.
            response = conversation.assistant_send('Please handle this request', task='business')
        self.assertNotEqual(response['messages'][-1]['role'], 'error', response['messages'][-1]['content'])
        return response

    def _stage(self, conversation, tool, arguments):
        response = self._send(conversation, tool, arguments)
        self.assertEqual(len(response['pending_operations']), 1)
        return response['pending_operations'][0]['id']

    def test_builder_selects_operations_and_preserves_skill_actions(self):
        existing = self._tool('count')
        wizard = self.env['qing.ai.tool.builder'].create({
            'model_id': self.model.id, 'skill_id': self.skill.id,
            'read_field_ids': [Command.set(self.read_fields.ids)],
            'write_field_ids': [Command.set(self.read_fields.ids)],
            'use_count': True, 'use_create': True, 'use_write': True,
        })
        action = wizard.action_generate()
        tools = self.env['qing.ai.tool'].search(action['domain'])
        self.assertEqual(set(tools.mapped('orm_operation')), {'search', 'count', 'create', 'write'})
        self.assertEqual(len(set(tools.mapped('key'))), 4)
        self.assertIn(existing.sudo(), self.skill.tool_ids)
        self.assertEqual(len(self.skill.tool_ids), 5)
        self.assertEqual(tools.available_for_user(), tools)

    def test_builder_form_onchange_and_view(self):
        with Form(self.env['qing.ai.tool.builder']) as form:
            form.model_id = self.model
            form.skill_id = self.skill
            self.assertIn(self.model.field_id.filtered(lambda f: f.name == 'name'), form.read_field_ids)
        action = form.record.action_generate()
        self.assertEqual(len(self.env['qing.ai.tool'].search(action['domain'])), 1)

    def test_all_fields_queries_and_filters_without_manual_selection(self):
        tool = self._tool('search', orm_all_fields=True, orm_field_ids=[Command.clear()])
        result = tool._orm_query({'domain': [['color', '=', 1], ['id', '=', self.tag.id]]})
        self.assertIn('"color": 1', result)
        self.assertIn('AI ORM Existing', result)
        self.assertIn('color', self._conversation()._business_tool_descriptions()[tool.key])
        self.assertNotIn('partner_ids', tool._orm_metadata(tool._orm_model()))

    def test_catalog_lists_model_fields_once_for_multiple_tools(self):
        first = self._tool('search', orm_all_fields=True)
        second = self._tool('count', orm_all_fields=True)
        self.skill.tool_ids = [Command.set([first.id, second.id])]
        catalog = self._conversation()._business_tool_descriptions()
        descriptions = [catalog[first.key], catalog[second.key]]
        self.assertEqual(sum('color|integer|' in item for item in descriptions), 1)
        self.assertEqual(sum('same model catalog as' in item for item in descriptions), 1)

    def test_all_fields_skips_inaccessible_computations_without_sudo(self):
        tool = self._tool('search', orm_all_fields=True)
        Model = tool._orm_model()
        original = type(Model).read

        def protected_read(records, fields=None, load='_classic_read'):
            self.assertFalse(records.env.su)
            if 'color' in (fields or []):
                raise AccessError('A dependency is not readable')
            return original(records, fields, load=load)

        with patch.object(type(Model), 'read', protected_read):
            reply = tool._orm_query({'domain': [['id', '=', self.tag.id]]})
            self.assertIn('AI ORM Existing', reply)
            self.assertIn('"unavailable_fields": ["color"]', reply)
            # Pending writes use the same safe read path for their snapshot.
            tool._orm_fingerprint(Model.browse(self.tag.id))
            tool.sudo().orm_all_fields = False
            with self.assertRaises(AccessError):
                tool._orm_query({'domain': [['id', '=', self.tag.id]]})

    def test_all_fields_partner_country_is_available(self):
        editor = self.env.ref('qing_partner.group_partner_editor', raise_if_not_found=False)
        if editor:
            self.user.group_ids = [Command.link(editor.id)]
        partner = self.env['res.partner'].with_user(self.user).create({
            'name': 'AI All Fields Customer', 'country_id': self.env.ref('base.cn').id,
        })
        tool = self._tool('search', model_id=self.env['ir.model']._get_id('res.partner'),
                          orm_all_fields=True, orm_field_ids=[Command.clear()])
        metadata = tool._orm_metadata(tool._orm_model())
        self.assertIn('country_id', metadata)
        self.assertNotIn('image_1920', metadata)
        self.assertNotIn('child_ids', metadata)
        result = tool._orm_query({
            'domain': [['id', '=', partner.id], ['country_id', '=', partner.country_id.id]],
            'fields': ['name', 'country_id'],
        })
        self.assertIn('AI All Fields Customer', result)
        self.assertIn('"country_id":', result)

    def test_all_fields_supports_readable_many2one_filter_paths(self):
        state = self.env['res.country.state'].create({
            'name': 'AI Shandong', 'code': 'AIS', 'country_id': self.env.ref('base.cn').id,
        })
        partner = self.env['res.partner'].create({
            'name': 'AI Province Customer', 'country_id': self.env.ref('base.cn').id,
            'state_id': state.id,
        })
        tool = self._tool(
            'search', model_id=self.env['ir.model']._get_id('res.partner'),
            orm_all_fields=True, orm_field_ids=[Command.clear()],
        )
        result = tool._orm_query({
            'domain': [['state_id.name', 'ilike', 'Shandong']],
            'fields': ['name', 'state_id'],
        })
        self.assertIn('AI Province Customer', result)
        self.assertNotIn('AI ORM Existing', result)
        with self.assertRaises(ValidationError):
            tool._orm_query({'domain': [['state_id.country_id.name.code', '=', 'CN']]})

    def test_all_fields_respects_caller_field_permissions_and_sensitive_filter(self):
        tool = self._tool('search', orm_all_fields=True)
        Model = tool._orm_model()
        with patch.object(Model._fields['color'], 'groups', 'base.group_system'):
            self.assertIn('color', tool.sudo()._orm_metadata(Model.sudo()))
            self.assertNotIn('color', tool._orm_metadata(Model))
            with self.assertRaises(ValidationError):
                tool._orm_query({'domain': [['color', '=', 1]]})
        original = type(Model).fields_get

        def metadata_with_new_fields(records, *args, **kwargs):
            result = original(records, *args, **kwargs)
            result.update({
                'x_new_field': {'string': 'New Field', 'type': 'char'},
                'x_api_key': {'string': 'API Key', 'type': 'char'},
                'x_document': {'string': 'Document', 'type': 'binary'},
            })
            return result

        with patch.object(type(Model), 'fields_get', metadata_with_new_fields):
            metadata = tool._orm_metadata(Model)
            self.assertIn('x_new_field', metadata)
            self.assertNotIn('x_api_key', metadata)
            self.assertNotIn('x_document', metadata)

    def test_all_fields_keeps_manual_selection_and_write_whitelist(self):
        tool = self._tool('write', orm_all_fields=True,
                          orm_field_ids=[Command.set(self.read_fields.filtered(lambda f: f.name == 'name').ids)],
                          orm_write_field_ids=[Command.set(self.read_fields.filtered(lambda f: f.name == 'name').ids)])
        self.assertIn('color', tool._orm_metadata(tool._orm_model()))
        with self.assertRaises(ValidationError):
            tool._orm_prepare({'record_id': self.tag.id, 'values': {'color': 7}})
        tool.sudo().orm_all_fields = False
        self.assertEqual(set(tool._orm_metadata(tool._orm_model())), {'name'})

    def test_all_fields_change_invalidates_pending_confirmation(self):
        tool = self._tool('write')
        conversation = self._conversation()
        operation = self._stage(conversation, tool, {'record_id': self.tag.id, 'values': {'color': 7}})
        tool.sudo().orm_all_fields = True
        with self.assertRaisesRegex(UserError, 'changed'):
            conversation.assistant_confirm_operation(operation)
        self.assertEqual(self.tag.color, 1)

    def test_builder_all_fields_option(self):
        with Form(self.env['qing.ai.tool.builder']) as form:
            form.model_id = self.model
            form.all_read_fields = True
            form.use_count = True
        action = form.record.action_generate()
        tools = self.env['qing.ai.tool'].search(action['domain'])
        self.assertEqual(len(tools), 2)
        self.assertTrue(all(tools.mapped('orm_all_fields')))
        self.assertIn('color', tools[0].with_user(self.user)._orm_metadata(self.env['res.partner.category'].with_user(self.user)))
        with Form(tools.filtered(lambda t: t.orm_operation == 'search')) as form:
            form.orm_all_fields = False
        self.assertEqual(form.record.orm_field_ids.mapped('name'), ['name'])

    def test_regular_user_cannot_configure_tools(self):
        tool = self._tool('count')
        with self.assertRaises(AccessError):
            tool.write({'orm_limit': 100})
        with self.assertRaises(AccessError):
            self.env['qing.ai.tool.builder'].with_user(self.user).create({'model_id': self.model.id})
        with self.assertRaises(AccessError):
            tool.action_open_builder()

    def test_search_and_count_intersect_fixed_scope(self):
        tool = self._tool('search', orm_domain=json.dumps([['id', '=', self.tag.id]]), orm_limit=1)
        result = tool._orm_query({'domain': [['name', 'ilike', 'Existing']]})
        self.assertIn('AI ORM Existing', result)
        self.assertIn('"count": 1', result)
        self.assertIn('"count": 0', tool._orm_query({'domain': [['name', '=', 'other']]}))
        self.assertIn('"count": 1', self._tool('count', orm_domain=tool.orm_domain)._orm_query({}))

    def test_read_details_and_record_scope(self):
        tool = self._tool('read', orm_domain=json.dumps([['color', '=', 1]]))
        self.assertIn(self.tag.name, tool._orm_query({'record_id': self.tag.id}))
        with self.assertRaises(AccessError):
            tool._orm_query({'record_id': 2147483647})

    def test_create_preview_confirmation_and_idempotency(self):
        tool = self._tool('create')
        conversation = self._conversation()
        operation = self._stage(conversation, tool, {'values': {'name': 'AI ORM Created', 'color': 2}})
        self.assertFalse(self.env['res.partner.category'].search([('name', '=', 'AI ORM Created')]))
        conversation.assistant_confirm_operation(operation)
        conversation.assistant_confirm_operation(operation)
        created = self.env['res.partner.category'].search([('name', '=', 'AI ORM Created')])
        self.assertEqual(len(created), 1)
        self.assertEqual(created.color, 2)
        self.assertFalse(conversation._pending_business_operations())

    def test_write_requires_confirmation(self):
        tool = self._tool('write')
        conversation = self._conversation()
        self._stage(conversation, tool, {'record_id': self.tag.id, 'values': {'color': 7}})
        self.assertEqual(self.tag.color, 1)
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text') as complete:
            conversation.assistant_send('确认')
        complete.assert_not_called()
        self.assertEqual(self.tag.color, 7)

    def test_changed_record_rejects_stale_preview(self):
        conversation = self._conversation()
        operation = self._stage(conversation, self._tool('write'), {
            'record_id': self.tag.id, 'values': {'color': 7},
        })
        self.tag.color = 3
        with self.assertRaisesRegex(UserError, 'changed'):
            conversation.assistant_confirm_operation(operation)
        self.assertEqual(self.tag.color, 3)

    def test_changed_configuration_and_revoked_skill_block_confirmation(self):
        conversation = self._conversation()
        tool = self._tool('write')
        operation = self._stage(conversation, tool, {'record_id': self.tag.id, 'values': {'color': 7}})
        tool.sudo().orm_limit = 19
        with self.assertRaisesRegex(UserError, 'changed'):
            conversation.assistant_confirm_operation(operation)
        self.skill.tool_ids = [Command.clear()]
        with self.assertRaises(AccessError):
            conversation.assistant_confirm_operation(operation)

    def test_confirmation_owner_expiry_and_operation_acl(self):
        conversation = self._conversation()
        operation_id = self._stage(conversation, self._tool('write'), {'record_id': self.tag.id, 'values': {'color': 7}})
        operation = self.env['qing.ai.operation'].browse(int(operation_id[4:]))
        with self.assertRaises(AccessError):
            conversation.with_user(self.other).assistant_confirm_operation(operation_id)
        self.assertFalse(self.env['qing.ai.operation'].with_user(self.other).search([('id', '=', operation.id)]))
        with self.assertRaises(AccessError):
            operation.with_user(self.user).write({'state': 'done'})
        operation.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        with self.assertRaisesRegex(UserError, 'expired'):
            conversation.assistant_confirm_operation(operation_id)

    def test_archive_restore_and_delete(self):
        conversation = self._conversation()
        for key, expected in [('archive', False), ('restore', True)]:
            tool = self._tool(key)
            operation = self._stage(conversation, tool, {'record_id': self.tag.id})
            self.assertEqual(self.tag.active, not expected)
            conversation.assistant_confirm_operation(operation)
            self.assertEqual(self.tag.active, expected)
        tool = self._tool('unlink')
        operation = self._stage(conversation, tool, {'record_id': self.tag.id})
        self.assertTrue(self.tag.exists())
        conversation.assistant_confirm_operation(operation)
        self.assertFalse(self.tag.exists())
        conversation.assistant_confirm_operation(operation)

    def test_invalid_domains_and_arguments_rejected(self):
        tool = self._tool('search')
        for domain in ({}, '[]', [['name', [], 'bad']], [['parent_id.name', '=', 'secret']], [['password', '=', 'secret']]):
            with self.subTest(domain=domain), self.assertRaises(ValidationError):
                tool._orm_query({'domain': domain})
        tool = self._tool('write')
        for values in ({'id': 1}, {'color': 'bad'}, {'name': ['bad']}, {'active': False}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                tool._orm_prepare({'record_id': self.tag.id, 'values': values})

    def test_technical_models_and_workflow_writes_rejected(self):
        for name in ('ir.config_parameter', 'res.users', 'res.company'):
            with self.subTest(model=name), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._tool('count', model_id=self.env['ir.model']._get_id(name), orm_field_ids=[Command.clear()])
        if 'sale.order' in self.env:
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._tool('create', model_id=self.env['ir.model']._get_id('sale.order'))

    def test_fixed_values_cannot_be_overridden(self):
        tool = self._tool('create', orm_defaults='{"color": 3}')
        normalized, _records, _preview = tool._orm_prepare({'values': {'name': 'AI Fixed'}})
        self.assertEqual(normalized['values']['color'], 3)
        with self.assertRaises(ValidationError):
            tool._orm_prepare({'values': {'name': 'AI Fixed', 'color': 4}})

    def test_failed_scope_validation_rolls_back_created_record(self):
        conversation = self._conversation()
        tool = self._tool('create', orm_domain='[["color", "=", 9]]')
        operation = self._stage(conversation, tool, {'values': {'name': 'AI Out Of Scope', 'color': 1}})
        with self.assertRaises(UserError):
            conversation.assistant_confirm_operation(operation)
        self.assertFalse(self.env['res.partner.category'].search([('name', '=', 'AI Out Of Scope')]))

    def test_company_scope_is_applied_with_multiple_active_companies(self):
        second = self.env['res.company'].create({'name': 'AI ORM Other Company'})
        self.user.company_ids = [Command.link(second.id)]
        partner = self.env['res.partner'].create({'name': 'AI Other Company', 'company_id': second.id})
        model = self.env['ir.model']._get('res.partner')
        tool = self._tool('count', model_id=model.id,
                          orm_field_ids=[Command.set(model.field_id.filtered(lambda f: f.name == 'name').ids)])
        tool = tool.with_context(allowed_company_ids=[self.env.company.id, second.id])
        self.assertIn('"count": 0', tool._orm_query({'domain': [['id', '=', partner.id]]}))

    def test_readonly_user_cannot_execute_a_write_tool(self):
        tool = self._tool('write')
        if self.env['res.partner.category'].with_user(self.other).has_access('write'):
            self.skipTest('Native category ACL grants this user write access.')
        conversation = self.env['qing.ai.conversation'].with_user(self.other).create({'agent_id': self.agent.id})
        self.assertNotIn(tool.key, conversation._business_tool_descriptions())

    def test_only_the_agent_catalog_can_be_called(self):
        tool = self._tool('count')
        conversation = self._conversation()
        self.skill.tool_ids = [Command.clear()]
        with self.assertRaises(UserError):
            conversation._parse_business_plan(json.dumps({'tool': tool.key, 'arguments': {}}))

    def test_query_ids_are_available_for_followup_requests(self):
        conversation = self._conversation()
        tool = self._tool('search')
        self._send(conversation, tool, {'domain': [['id', '=', self.tag.id]]})
        prompt = conversation._business_prompt('Edit that tag', {tool.key}, None)
        self.assertIn('AI ORM Existing', prompt)
        self.assertIn('query IDs', prompt)

    def test_background_run_uses_query_result_for_a_second_model_turn(self):
        tool = self._tool('count')
        conversation = self._conversation()
        plans = [
            {'text': json.dumps({
                'tool': tool.key,
                'arguments': {},
                'progress': 'Checking the matching records.',
            }), 'model': 'mock', 'usage': {
                'input_tokens': 2, 'output_tokens': 1, 'total_tokens': 3,
                'output_tokens_details': {'reasoning_tokens': 1},
            }},
            {'text': json.dumps({
                'answer': 'There is one matching tag.',
                'progress': 'Preparing the verified total.',
            }), 'model': 'mock', 'usage': {
                'input_tokens': 3, 'output_tokens': 1, 'total_tokens': 4,
                'output_tokens_details': {'reasoning_tokens': 2},
            }},
        ]
        with patch(
            'odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text',
            side_effect=plans,
        ) as complete:
            opened = conversation.assistant_send(
                'How many matching tags are there?', task='business', background=True,
            )
            self.assertTrue(opened['active_run'])
            self.assertEqual(complete.call_count, 0)
            first = conversation.assistant_continue_run(opened['active_run']['id'])
            self.assertTrue(first['active_run'])
            public_steps = first['active_run']['process_steps']
            self.assertEqual(public_steps[0]['summary'], 'Checking the matching records.')
            self.assertEqual(public_steps[0]['status'], 'ok')
            self.assertEqual(public_steps[-1]['status'], 'running')
            self.assertFalse({'arguments', 'result', 'error'} & set(public_steps[0]))
            final = conversation.assistant_continue_run(opened['active_run']['id'])
        self.assertFalse(final['active_run'])
        self.assertEqual(final['messages'][-1]['content'], 'There is one matching tag.')
        self.assertEqual(final['messages'][-1]['usage']['total_tokens'], 7)
        self.assertEqual(final['messages'][-1]['usage_summary'], {
            'input_tokens': 5,
            'output_tokens': 2,
            'reasoning_tokens': 3,
            'total_tokens': 7,
        })
        self.assertEqual(
            [step['summary'] for step in final['messages'][-1]['process_steps']],
            ['Checking the matching records.', 'Preparing the verified total.'],
        )
        self.assertFalse(self.env['qing.ai.run'].sudo().search([
            ('conversation_id', '=', conversation.id),
        ]))
        self.assertIn('Query result', complete.call_args_list[1].args[0])
        self.assertIn('\\"count\\": 1', complete.call_args_list[1].args[0])

    def test_business_plan_accepts_safe_progress_and_rejects_invalid_progress(self):
        conversation = self._conversation()
        tool = self._tool('count')
        plan = conversation._parse_business_plan(json.dumps({
            'tool': tool.key,
            'arguments': {},
            'progress': 'Checking matching records.',
        }))
        self.assertEqual(plan['progress'], 'Checking matching records.')
        with self.assertRaises(UserError):
            conversation._parse_business_plan(json.dumps({
                'tool': tool.key, 'arguments': {}, 'progress': {'private': 'reasoning'},
            }))
        with self.assertRaises(UserError):
            conversation._parse_business_plan(json.dumps({
                'answer': 'Done', 'progress': 'x' * 301,
            }))

    def test_plain_answer_cannot_claim_a_generic_write_preview(self):
        conversation = self._conversation()
        self._tool('create')
        result = {'text': '{"answer": "Created the tag."}', 'model': 'mock', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=result):
            reply = conversation.assistant_send('Create tag Example')
        self.assertIn('No business action was staged', reply['messages'][-1]['content'])
        self.assertFalse(reply['pending_operations'])
