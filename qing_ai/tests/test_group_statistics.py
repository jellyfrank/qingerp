import json
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiGroupStatistics(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='ai-statistics-user', groups='base.group_user,base.group_partner_manager')
        cls.model = cls.env['ir.model']._get('res.partner.category')
        cls.tags = cls.env['res.partner.category'].create([
            {'name': 'AI Group Fixture %s' % index, 'color': index % 3} for index in range(145)
        ])

    def _tool(self, **extra):
        values = {
            'name': 'Tag Distribution', 'execution_type': 'orm', 'model_id': self.model.id,
            'orm_operation': 'read_group', 'orm_domain': '[["name", "ilike", "AI Group Fixture"]]',
            'orm_group_field_ids': [Command.set(self.model.field_id.filtered(lambda f: f.name == 'color').ids)],
            'orm_measure_field_ids': [Command.set(self.model.field_id.filtered(lambda f: f.name == 'color').ids)],
        }
        values.update(extra)
        return self.env['qing.ai.tool'].create(values).with_user(self.user)

    def test_database_aggregation_counts_all_records_not_query_limit(self):
        tool = self._tool(orm_limit=3)
        Model = tool._orm_model()
        domain, groupby, aggregates = tool._group_arguments(Model, {})
        rows = Model._read_group(domain, groupby, aggregates)
        self.assertEqual(sum(row[1] for row in rows), 145)
        self.assertEqual(sum(row[2] for row in rows), sum(self.tags.mapped('color')))
        report = tool._orm_query({})
        self.assertIn('Matching records: 145', report)
        self.assertIn('| 0 | 49 | 0 |', report)
        self.assertNotIn('Only the first', report)

    def test_limits_are_explicit_and_empty_results_are_clear(self):
        tool = self._tool(orm_limit=2)
        self.assertIn('Only the first 2 groups', tool._orm_query({}))
        report = tool._orm_query({'domain': [['color', '=', 50]]})
        self.assertIn('Matching records: 0', report)
        self.assertIn('No matching records', report)

    def test_allowed_functions_and_fixed_scope(self):
        tool = self._tool()
        report = tool._orm_query({'aggregates': ['color:avg', 'color:min', 'color:max'], 'domain': [['color', '=', 2]]})
        self.assertIn('Matching records: 48', report)
        self.assertIn('Average', report)
        with self.assertRaises(ValidationError):
            tool._orm_query({'domain': [['name', '=', 'outside']]})
        Model = tool._orm_model()
        domain, _, _ = tool._group_arguments(Model, {'domain': [['color', '=', 999]]})
        self.assertFalse(Model.search(domain))

    def test_rejects_unknown_fields_functions_and_raw_expressions(self):
        tool = self._tool()
        for args in ({'groupby': ['name']}, {'aggregates': ['color:array_agg']}, {'groupby': 'color'},
                     {'groupby': []}, {'aggregates': []}, {'groupby': ['color', 'color']},
                     {'aggregates': ['password:sum']}, {'sql': 'SELECT 1'}, {'domain': [['id', 'any!', []]]}):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                tool._orm_query(args)

    def test_configuration_rejects_wrong_or_nonstored_fields(self):
        bad = self.env['ir.model.fields'].search([('model', '=', 'res.partner'), ('name', '=', 'country_id')])
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._tool(orm_group_field_ids=[Command.set(bad.ids)])
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._tool(orm_group_field_ids=[Command.clear()])
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._tool(orm_measure_field_ids=[Command.set(self.model.field_id.filtered(lambda f: f.name == 'name').ids)])

    def test_group_fields_respect_caller_permissions(self):
        tool = self._tool()
        Model = tool._orm_model()
        Model.env._field_access_memo.clear()
        with patch.object(Model._fields['color'], 'groups', 'base.group_system'):
            Model.env._field_access_memo.clear()
            with self.assertRaises(ValidationError):
                tool._orm_query({})
        Model.env._field_access_memo.clear()

    def test_all_fields_grouping_needs_no_configured_field_lists(self):
        tool = self._tool(
            orm_all_fields=True,
            orm_group_field_ids=[Command.clear()],
            orm_measure_field_ids=[Command.clear()],
        )
        schema = tool._orm_schema()
        self.assertEqual(schema['required'], ['groupby'])
        self.assertIn('color', schema['properties']['groupby']['items']['enum'])
        report = tool._orm_query({'groupby': ['color'], 'aggregates': ['__count']})
        self.assertIn('Matching records: 145', report)
        self.assertIn('| 0 | 49 |', report)

    def test_all_fields_can_keep_reviewed_default_groups(self):
        tool = self._tool(orm_all_fields=True)
        self.assertEqual(tool._orm_schema()['required'], [])
        report = tool._orm_query({})
        self.assertIn('Matching records: 145', report)
        self.assertIn('Color', report)
        self.assertIn('Sum', report)

    def test_builder_and_tool_form(self):
        with Form(self.env['qing.ai.tool.builder']) as form:
            form.model_id = self.model
            form.all_read_fields = False
            form.use_search = False
            form.use_read_group = True
            form.group_field_ids.add(self.model.field_id.filtered(lambda f: f.name == 'color'))
        action = form.record.action_generate()
        tool = self.env['qing.ai.tool'].search(action['domain'])
        self.assertEqual(tool.orm_operation, 'read_group')
        with Form(tool) as form:
            form.orm_limit = 4
        self.assertEqual(tool.orm_limit, 4)
        self.assertIn('__count', tool._orm_schema()['properties']['aggregates']['items']['enum'])

    def test_agent_dispatch_produces_a_table_without_write_confirmation(self):
        tool = self._tool()
        skill = self.env['qing.ai.skill'].create({'name': 'Stats', 'tool_ids': [Command.link(tool.id)]})
        agent = self.env['qing.ai.agent'].create({'name': 'Stats', 'skill_ids': [Command.link(skill.id)]})
        conversation = self.env['qing.ai.conversation'].with_user(self.user).create({'agent_id': agent.id})
        reply = {'text': json.dumps({'tool': tool.key, 'arguments': {}}), 'model': 'mock', 'usage': {}}
        with patch('odoo.addons.qing_ai.models.qing_ai_service.QingAiService._complete_text', return_value=reply):
            response = conversation.assistant_send('Group the records by color', task='business')
        self.assertIn('Matching records: 145', response['messages'][-1]['content'])
        self.assertFalse(response['pending_operations'])
