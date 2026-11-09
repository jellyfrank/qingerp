from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingAiTokenBudget(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='ai-token-user', groups='base.group_user,base.group_partner_manager')
        cls.model = cls.env['ir.model']._get('res.partner')

    def _tool(self, operation):
        return self.env['qing.ai.tool'].create({
            'name': 'Partners ' + operation, 'execution_type': 'orm', 'model_id': self.model.id,
            'orm_operation': operation, 'orm_all_fields': True,
        }).with_user(self.user)

    def test_catalog_skips_helper_fields_but_validation_keeps_them(self):
        tool = self._tool('search')
        Partner = tool._orm_model()
        readable = tool._orm_metadata(Partner)
        catalog = tool._orm_catalog_metadata(Partner)
        self.assertIn('name', catalog)
        self.assertIn('country_id', catalog)
        self.assertLess(len(catalog), len(readable))
        for name in catalog:
            field = Partner._fields[name]
            self.assertFalse(name.startswith(('message_', 'activity_')), name)
            self.assertTrue(field.store or field.related or field.search, name)
        description = tool._orm_catalog_description()
        hidden = sorted(set(readable) - set(catalog))
        self.assertTrue(hidden)
        self.assertNotIn(hidden[0] + '|', description)
        # A field left out of the catalog is still a valid, readable argument.
        normalized, _records, _preview = tool._orm_prepare({'fields': ['name', hidden[0]]})
        self.assertEqual(normalized['fields'], ['name', hidden[0]])
        default_fields = tool._orm_requested_fields(Partner)
        self.assertTrue(set(default_fields) <= set(catalog))

    def test_configured_fields_are_listed_unchanged(self):
        fields = self.model.field_id.filtered(lambda f: f.name in ('name', 'message_is_follower'))
        tool = self.env['qing.ai.tool'].create({
            'name': 'Partners configured', 'execution_type': 'orm', 'model_id': self.model.id,
            'orm_operation': 'search', 'orm_field_ids': [(6, 0, fields.ids)],
        }).with_user(self.user)
        Partner = tool._orm_model()
        self.assertEqual(set(tool._orm_catalog_metadata(Partner)), set(tool._orm_metadata(Partner)))

    def test_large_group_schema_uses_rules_and_still_validates(self):
        tool = self._tool('read_group')
        Partner = tool._orm_model()
        groups, _measures = tool._group_options(Partner)
        self.assertGreater(len(groups), 20)
        schema = tool._orm_schema()
        groupby = schema['properties']['groupby']
        self.assertNotIn('enum', groupby['items'])
        self.assertIn(':month', groupby['description'])
        domain, groupby_values, aggregates = tool._group_arguments(
            Partner, {'groupby': ['create_date:month'], 'aggregates': ['__count']},
        )
        self.assertEqual(groupby_values, ['create_date:month'])
        with self.assertRaises(ValidationError):
            tool._group_arguments(Partner, {'groupby': ['create_date'], 'aggregates': ['__count']})
        with self.assertRaises(ValidationError):
            tool._group_arguments(Partner, {'groupby': ['no_such_field'], 'aggregates': ['__count']})

    def test_usage_summary_reports_prompt_cache_hits(self):
        service = self.env['qing.ai.service']
        self.assertEqual(service._usage_summary({
            'prompt_tokens': 1000, 'completion_tokens': 20, 'total_tokens': 1020,
            'prompt_cache_hit_tokens': 900, 'prompt_cache_miss_tokens': 100,
        })['cached_input_tokens'], 900)
        self.assertEqual(service._usage_summary({
            'input_tokens': 50, 'input_tokens_details': {'cached_tokens': 32},
        })['cached_input_tokens'], 32)
        self.assertEqual(service._usage_summary({
            'promptTokenCount': 70, 'cachedContentTokenCount': 64,
        })['cached_input_tokens'], 64)
        self.assertNotIn('cached_input_tokens', service._usage_summary({'prompt_tokens': 10}))
