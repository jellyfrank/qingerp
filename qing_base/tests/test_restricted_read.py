import logging
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged


ADDRESS_FIELDS = ('contact_address', 'address', 'contact_address_inline', 'address_inline')
RESTRICTED_KEY = '__qing_restricted_fields'
_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestRestrictedRead(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = new_test_user(
            cls.env, login='qing_partial_reader',
            groups='base.group_user,base.group_partner_manager',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.other_company = cls.env['res.company'].create({'name': 'Restricted compute other company'})
        cls.secret = cls.env['res.partner'].create({
            'name': 'Private source address', 'company_id': cls.other_company.id,
        })
        cls.partners = cls.env['res.partner'].create([
            {'name': 'Readable compute', 'street': 'Accessible address'},
            {'name': 'Restricted compute', 'street': 'Original address'},
        ])
        cls.bad_id = cls.partners[1].id
        cls.partner_type = type(cls.env['res.partner'])
        cls.original_compute = cls.partner_type._compute_address

    def setUp(self):
        super().setUp()
        self.records = self.partners.with_user(self.operator).with_context(
            allowed_company_ids=self.env.company.ids, qing_partial_read=True,
        )
        self.specification = {'id': {}, 'name': {}, 'contact_address': {}, 'address': {}}

    def _patch_address(self, exception=None, side_effect=False):
        bad_id = self.bad_id
        secret_id = self.secret.id
        original_compute = type(self).original_compute

        def compute(records):
            original_compute(records)
            for record in records:
                if record.id != bad_id:
                    continue
                record.contact_address = 'Incomplete private result'
                if side_effect:
                    record.phone = 'Failed compute side effect'
                if exception:
                    raise exception('Unexpected computation failure')
                record.env['res.partner'].browse(secret_id).read(['name'])

        self.env.invalidate_all()
        return patch.object(self.partner_type, '_compute_address', compute)

    def test_plain_callers_receive_standard_or_access_error(self):
        normal = self.records[0].with_context(qing_partial_read=False)
        self.assertNotIn(RESTRICTED_KEY, normal.web_read(self.specification)[0])
        with self._patch_address(), self.assertRaises(AccessError):
            self.records[1].with_context(qing_partial_read=False).web_read(self.specification)

    def test_recovered_complete_record_does_not_add_empty_marker(self):
        from odoo.addons.qing_base.tools import RestrictedComputeError

        error = RestrictedComputeError((), AccessError('Transient restriction'))
        result = self.records[0]._qing_partial_record({'name': {}}, error)[0]
        self.assertEqual(result['name'], 'Readable compute')
        self.assertNotIn(RESTRICTED_KEY, result)

    def test_native_form_raises_access_error_for_restricted_compute(self):
        view = self.env['ir.ui.view'].create({
            'name': 'qing restricted partner test form',
            'model': 'res.partner',
            'type': 'form',
            'arch': '<form><field name="name"/><field name="contact_address"/></form>',
        })
        with self._patch_address(), self.assertRaises(AccessError):
            Form(self.records[1].with_context(qing_partial_read=False), view=view.id)

    def test_mixed_batch_preserves_good_record_and_masks_siblings(self):
        with self._patch_address():
            values = self.records.web_read(self.specification)
        self.assertEqual([value['id'] for value in values], self.records.ids)
        self.assertIn('Accessible address', values[0]['contact_address'])
        self.assertNotIn(RESTRICTED_KEY, values[0])
        self.assertEqual(values[1]['name'], 'Restricted compute')
        self.assertEqual(values[1][RESTRICTED_KEY], ['address', 'contact_address'])
        self.assertNotIn('contact_address', values[1])
        self.assertNotIn('address', values[1])
        self.assertNotIn('Private source address', str(values))
        self.assertNotIn('Incomplete private result', str(values))

    def test_search_fetch_failure_keeps_count_offset_order_and_good_rows(self):
        with self._patch_address():
            values = self.records.web_search_read(
                [('id', 'in', self.records.ids)], self.specification, limit=1, order='id desc',
            )
            self.assertEqual(values['length'], 2)
            self.assertEqual(values['records'][0]['id'], self.bad_id)
            self.assertNotIn('contact_address', values['records'][0])
            values = self.records.web_search_read(
                [('id', 'in', self.records.ids)], self.specification,
                offset=1, limit=1, order='id desc',
            )
            self.assertEqual(values['length'], 2)
            self.assertNotIn(RESTRICTED_KEY, values['records'][0])
            self.assertIn('Accessible address', values['records'][0]['contact_address'])

    def test_failed_compute_cache_and_database_writes_are_rolled_back(self):
        with self._patch_address(side_effect=True):
            result = self.records[1].web_read(self.specification)[0]
            self.assertNotIn('contact_address', result)
            self.assertFalse(self.records[1].phone)
            with self.assertRaises(AccessError):
                self.records[1].read(['contact_address'])

    def test_normal_read_and_forged_context_keep_original_access_error(self):
        with self._patch_address():
            with self.assertRaises(AccessError):
                self.records[1].read(['contact_address'])
            self.env.invalidate_all()
            with self.assertRaises(AccessError):
                self.records[1].with_context(
                    qing_partial_read=True, qing_observe_computes=True,
                ).read(['contact_address'])

    def test_top_level_record_access_is_not_tolerated_even_for_id_only(self):
        secret = self.secret.with_user(self.operator).with_context(allowed_company_ids=self.env.company.ids)
        for specification in ({'id': {}}, {'name': {}, 'contact_address': {}}):
            with self.assertRaises(AccessError):
                secret.web_read(specification)

    def test_field_group_access_is_not_tolerated(self):
        field = self.env['res.partner']._fields['contact_address']
        with patch.object(field, 'groups', 'base.group_system'):
            with self.assertRaises(AccessError):
                self.records.web_read(self.specification)

    def test_other_compute_exceptions_are_not_tolerated(self):
        for exception in (ValidationError, RuntimeError):
            with self._patch_address(exception=exception):
                with self.assertRaises(exception):
                    self.records[1].web_read(self.specification)

    def test_nested_dynamic_dependency_is_detected_without_declaring_a_policy(self):
        def compute_name(records):
            for record in records:
                record.display_name = record.contact_address

        with self._patch_address(), patch.object(self.partner_type, '_compute_display_name', compute_name):
            value = self.records[1].web_read({'name': {}, 'display_name': {}})[0]
        self.assertEqual(value['name'], 'Restricted compute')
        self.assertEqual(value[RESTRICTED_KEY], ['display_name'])
        self.assertNotIn('display_name', value)

    def test_declared_dependents_are_restricted_together(self):
        field = self.env['res.partner']._fields['tz_offset']
        with patch.dict(self.env.registry.field_depends, {field: ('contact_address',)}):
            with self._patch_address():
                value = self.records[1].web_read({'name': {}, 'contact_address': {}, 'tz_offset': {}})[0]
        self.assertEqual(value[RESTRICTED_KEY], ['contact_address', 'tz_offset'])
        self.assertNotIn('tz_offset', value)

    def test_nested_relation_is_unavailable_when_its_compute_fails(self):
        parent = self.env['res.partner'].create({'name': 'Root with restricted computed child', 'parent_id': self.bad_id})
        specification = {'name': {}, 'parent_id': {'fields': {'contact_address': {}}}}
        with self._patch_address():
            value = parent.with_user(self.operator).with_context(qing_partial_read=True).web_read(specification)[0]
        self.assertEqual(value['name'], parent.name)
        self.assertEqual(value[RESTRICTED_KEY], ['parent_id'])
        self.assertNotIn('parent_id', value)

    def test_web_save_does_not_turn_a_compute_error_into_a_success(self):
        from odoo.addons.qing_base.tools import are_computes_strict

        def compute(records):
            self.assertTrue(are_computes_strict())
            raise AccessError('Save result computation must remain strict')

        with patch.object(self.partner_type, '_compute_address', compute):
            self.records.invalidate_recordset(list(ADDRESS_FIELDS))
            with self.env.cr.savepoint(), self.assertRaises(AccessError):
                self.records[1].web_save({}, self.specification)

    def test_stored_computation_keeps_original_error(self):
        field = self.env['res.partner']._fields['complete_name']

        def compute(records):
            raise AccessError('Stored computation is strict')

        with patch.object(self.partner_type, '_compute_complete_name', compute):
            self.records[1].invalidate_recordset(['complete_name'])
            with self.assertRaises(AccessError):
                self.env.add_to_compute(field, self.records[1])
                self.records[1].web_read({'name': {}, 'complete_name': {}})

    def test_scope_does_not_survive_a_success_or_failure(self):
        from odoo.addons.qing_base.tools import is_observing_computes

        with self._patch_address():
            self.records.web_read(self.specification)
            self.assertFalse(is_observing_computes())
            with self.assertRaises(AccessError):
                self.records[1].contact_address
        self.assertFalse(is_observing_computes())

    def test_recovery_budget_rethrows_native_error_and_clears_scope(self):
        from odoo.addons.qing_base.tools import is_observing_computes
        from odoo.addons.qing_base.models import base

        specification = {'name': {}, 'phone': {}, 'contact_address': {}}
        with self._patch_address(), patch.object(base, 'MAX_READ_ATTEMPTS', 2):
            with self.assertRaises(AccessError):
                self.records[1].web_read(specification)
        self.assertIsNone(base.READ_BUDGET.get())
        self.assertFalse(is_observing_computes())

    def test_authorized_reader_still_receives_the_real_computation(self):
        with self._patch_address():
            value = self.partners[1].with_context(qing_partial_read=True).web_read(self.specification)[0]
        self.assertNotIn(RESTRICTED_KEY, value)
        self.assertEqual(value['contact_address'], 'Incomplete private result')

    def test_50_and_150_accessible_rows_use_one_batch_attempt(self):
        from odoo.addons.qing_base.models.base import READ_BUDGET, ReadBudget

        records = self.env['res.partner'].create([{'name': 'Batch %s' % index} for index in range(150)])
        query_counts = []
        for size in (50, 150):
            self.env.invalidate_all()
            budget = ReadBudget()
            token = READ_BUDGET.set(budget)
            try:
                before = self.env.cr.sql_log_count
                values = records[:size].with_user(self.operator).with_context(qing_partial_read=True).web_read(self.specification)
                query_counts.append(self.env.cr.sql_log_count - before)
            finally:
                READ_BUDGET.reset(token)
            self.assertEqual(len(values), size)
            self.assertEqual(budget.attempts, 1)
            self.assertTrue(all(RESTRICTED_KEY not in value for value in values))
            _logger.info('Partial-read successful batch: %s rows, %s SQL queries', size, query_counts[-1])
        self.assertLessEqual(query_counts[1], query_counts[0] + 5)
