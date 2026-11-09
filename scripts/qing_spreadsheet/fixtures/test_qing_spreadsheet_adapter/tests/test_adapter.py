import copy
import uuid
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestRecordAdapter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='adapter_owner', groups='base.group_user')
        cls.other = new_test_user(cls.env, login='adapter_other', groups='base.group_user')
        cls.record = cls.env['qing.spreadsheet.fixture'].with_user(cls.user).create({'name': 'Fixture', 'value': 12})
        action = cls.record.action_open_qing_spreadsheet()
        cls.book = cls.env['qing.spreadsheet'].with_user(cls.user).browse(action['params']['workbook_id'])

    def test_save_is_draft_apply_is_atomic_and_idempotent(self):
        token = self.book.acquire_lease()['token']
        data = self.book.open_workbook()['data']
        data['sheets'][0]['cells']['B1'] = '27'
        self.book.save_workbook(data, 0, token, str(uuid.uuid4()))
        self.assertEqual(self.record.value, 12)
        self.assertFalse(self.book.preview_record(data, 1, token)['errors'])
        request_id = str(uuid.uuid4())
        result = self.book.apply_record(data, 1, token, request_id)
        self.assertEqual(self.record.value, 27)
        self.assertEqual(self.record.apply_count, 1)
        self.assertEqual(self.book.apply_record(data, 1, token, request_id), result)
        self.assertEqual(self.record.apply_count, 1)
        self.assertEqual(self.book.save_status(request_id)['operation'], 'apply')
        self.assertEqual(self.book.content_version, 2)

    def test_stale_source_and_reload(self):
        token = self.book.acquire_lease()['token']
        data = self.book.open_workbook()['data']
        self.record.write({'value': 44})
        with self.assertRaises(UserError):
            self.book.apply_record(data, 0, token, str(uuid.uuid4()))
        self.book.reload_record(0, token, str(uuid.uuid4()))
        self.assertEqual(float(self.book.open_workbook()['data']['sheets'][0]['cells']['B1']), 44)
        self.assertEqual(self.record.apply_count, 0)

    def test_failed_validation_and_rollback(self):
        token = self.book.acquire_lease()['token']
        data = self.book.open_workbook()['data']
        data['sheets'][0]['cells']['B1'] = '-1'
        self.assertEqual(self.book.preview_record(data, 0, token)['errors'][0]['cell'], 'B1')
        with self.assertRaises(ValidationError):
            self.book.apply_record(data, 0, token, str(uuid.uuid4()))
        data['sheets'][0]['cells']['B1'] = '27'
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.book.with_context(test_fail_after_write=True).apply_record(data, 0, token, str(uuid.uuid4()))
        self.assertEqual(self.record.value, 12)
        self.assertEqual(self.record.apply_count, 0)
        self.assertEqual(self.book.content_version, 0)

    def test_binding_cannot_be_forged_and_copy_is_unbound(self):
        with self.assertRaises(AccessError):
            self.book.write({'binding_res_id': 42})
        with self.assertRaises(AccessError):
            self.record.with_user(self.other).action_open_qing_spreadsheet()
        duplicate = self.book.copy()
        self.assertFalse(duplicate.binding_model)
        token = duplicate.acquire_lease()['token']
        with self.assertRaises(UserError):
            duplicate.apply_record(duplicate.open_workbook()['data'], 0, token, str(uuid.uuid4()))
