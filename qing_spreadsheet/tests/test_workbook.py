import copy
import io
import uuid
import zipfile
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..services import workbook


@tagged('post_install', '-at_install')
class TestWorkbook(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login='sheet_unit_owner', groups='base.group_user')
        cls.editor = new_test_user(cls.env, login='sheet_unit_editor', groups='base.group_user')
        cls.viewer = new_test_user(cls.env, login='sheet_unit_viewer', groups='base.group_user')
        cls.outsider = new_test_user(cls.env, login='sheet_unit_outsider', groups='base.group_user')
        cls.Book = cls.env['qing.spreadsheet'].with_user(cls.owner)
        cls.book = cls.Book.create({'name': 'Test workbook', 'editor_ids': [Command.link(cls.editor.id)],
                                    'reader_ids': [Command.link(cls.viewer.id)]})

    def draft(self, value='12'):
        data = copy.deepcopy(self.book.open_workbook()['data'])
        data['sheets'][0]['cells'] = {'A1': value, 'A2': '=SUM(A1,3)'}
        return data

    def test_save_replay_conflict_and_restore(self):
        token = self.book.acquire_lease()['token']
        request_id = str(uuid.uuid4())
        data = self.draft()
        saved = self.book.save_workbook(data, 0, token, request_id)
        self.assertEqual(saved['version'], 1)
        self.assertTrue(self.book.save_workbook(data, 0, token, request_id)['replayed'])
        self.assertEqual(len(self.book.list_versions()), 2)
        with self.assertRaises(ValidationError):
            self.book.save_workbook(self.draft('99'), 0, token, request_id)
        with self.assertRaises(UserError):
            self.book.save_workbook(data, 0, token, str(uuid.uuid4()))
        self.assertTrue(self.book.save_status(request_id)['saved'])
        initial = self.book.list_versions()[-1]
        self.book.restore_version(initial['id'], 1, token, str(uuid.uuid4()))
        self.assertEqual(self.book.content_version, 2)
        self.assertFalse(self.book.open_workbook()['data']['sheets'][0].get('cells'))

    def test_acl_and_private_write_gate(self):
        for values in ({'spreadsheet_data': '{}'}, {'content_version': 42}, {'lease_user_id': self.editor.id},
                       {'favorite_user_ids': [Command.link(self.editor.id)]}):
            with self.assertRaises(AccessError):
                self.book.write(values)
            with self.assertRaises(AccessError):
                self.book.with_context(_qing_spreadsheet_internal=True).write(values)
        with self.assertRaises(AccessError):
            self.book.with_user(self.viewer).acquire_lease()
        with self.assertRaises(AccessError):
            self.book.with_user(self.outsider).open_workbook()
        with self.assertRaises(AccessError):
            self.book.with_user(self.editor).write({'reader_ids': [Command.link(self.outsider.id)]})
        with self.assertRaises(AccessError):
            self.Book.with_context(default_spreadsheet_data='{}').create({'name': 'Bypass'})
        self.assertFalse(self.book._fields['spreadsheet_binary_data'].attachment)
        self.assertEqual(self.book.with_user(self.viewer).open_workbook()['id'], self.book.id)
        self.book.with_user(self.viewer).action_toggle_favorite()
        self.assertTrue(self.book.with_user(self.viewer).is_favorite)
        revision = self.env['qing.spreadsheet.revision'].browse(self.book.list_versions()[0]['id'])
        with self.assertRaises(AccessError):
            revision.with_user(self.outsider).read(['data'])
        with self.assertRaises(AccessError):
            revision.with_user(self.owner).write({'data': '{}'})
        self.book.write({'reader_ids': [Command.clear()]})
        with self.assertRaises(AccessError):
            revision.with_user(self.viewer).read(['data'])

    def test_lease_ownership_expiry_and_permission_revocation(self):
        token = self.book.acquire_lease()['token']
        self.assertFalse(self.book.with_user(self.editor).acquire_lease()['acquired'])
        with self.assertRaises(UserError):
            self.book.with_user(self.editor).save_workbook(self.draft(), 0, token, str(uuid.uuid4()))
        self.book._internal_write({'lease_until': fields.Datetime.now() - timedelta(seconds=1)})
        with self.assertRaises(UserError):
            self.book.save_workbook(self.draft(), 0, token, str(uuid.uuid4()))
        editor_token = self.book.with_user(self.editor).acquire_lease()['token']
        self.book.write({'editor_ids': [Command.clear()]})
        with self.assertRaises(AccessError):
            self.book.with_user(self.editor).renew_lease(editor_token)

    def test_copy_and_company_boundary(self):
        token = self.book.acquire_lease()['token']
        self.book.save_workbook(self.draft(), 0, token, str(uuid.uuid4()))
        duplicate = self.book.with_user(self.viewer).copy()
        self.assertEqual(duplicate.owner_id, self.viewer)
        self.assertFalse(duplicate.editor_ids | duplicate.reader_ids)
        self.assertEqual(duplicate.content_version, 0)
        self.assertEqual(duplicate.open_workbook()['data'], self.book.open_workbook()['data'])
        company = self.env['res.company'].create({'name': 'Spreadsheet Other'})
        with self.assertRaises(AccessError):
            self.Book.create({'name': 'Forbidden', 'company_id': company.id})
        with self.assertRaises(ValidationError):
            self.book.write({'company_id': company.id})
        with self.assertRaises(AccessError):
            self.book.copy({'reader_ids': [Command.link(self.outsider.id)]})

    def test_source_policy_and_context(self):
        data = self.draft()
        data['lists'] = {'1': {'model': 'res.partner', 'name': 'Contacts', 'columns': ['name'], 'domain': [],
                                'context': {'allowed_company_ids': [99999], 'sudo': True}, 'orderBy': []}}
        token = self.book.acquire_lease()['token']
        self.book.save_workbook(data, 0, token, str(uuid.uuid4()))
        stored = self.book.open_workbook()['data']['lists']['1']
        self.assertEqual(stored['context'], {'allowed_company_ids': [self.book.company_id.id]})
        data['lists']['1']['columns'] = ['bank_ids']
        with self.assertRaises(ValidationError):
            self.book.save_workbook(data, 1, token, str(uuid.uuid4()))
        data['lists']['1']['model'] = 'res.users'
        with self.assertRaises(ValidationError):
            self.book.save_workbook(data, 1, token, str(uuid.uuid4()))

    def test_bounded_import_export(self):
        data = self.draft()
        workbook.validate(data)
        compressed = copy.deepcopy(data)
        compressed['sheets'][0]['cells'] = {'A1': '1', 'A2:A100': {'N': '+1'}}
        workbook.validate(compressed)
        compressed['sheets'][0]['cells'] = {'A1:Z10000': '1'}
        with self.assertRaises(ValidationError):
            workbook.validate(compressed)
        for mutation in ({'sheets': []}, {'constructor': {}}, {'sheets': [{'id': 'x', 'name': 'x', 'rowNumber': 100001}]}):
            invalid = {**data, **mutation}
            with self.assertRaises(ValidationError):
                workbook.validate(invalid)
        files = [{'path': '[Content_Types].xml', 'content': '<Types/>'}, {'path': 'xl/workbook.xml', 'content': '<workbook/>'}]
        self.assertEqual(workbook.unpack_xlsx(workbook.pack_xlsx(files))['xl/workbook.xml'], '<workbook/>')
        with self.assertRaises(ValidationError):
            workbook.pack_xlsx([{'path': '../escape', 'content': ''}])
        files.append({'path': 'xl/vbaProject.bin', 'content': 'macro'})
        with self.assertRaises(ValidationError):
            workbook.unpack_xlsx(workbook.pack_xlsx(files))
        with self.assertRaises(ValidationError):
            workbook.pack_xlsx([{'path': 'image.png', 'imageSrc': 'http://example.com/image.png'}])

    def test_query_policy_on_every_read(self):
        result = self.book.query_data('res.partner', 'web_search_read', [], {
            'domain': [], 'specification': {'name': {}}, 'limit': 10,
            'context': {'allowed_company_ids': [99999], 'active_test': False}})
        self.assertLessEqual(len(result['records']), 10)
        with self.assertRaises(ValidationError):
            self.book.query_data('res.partner', 'web_search_read', [], {
                'domain': [], 'specification': {'bank_ids': {'fields': {'acc_number': {}}}}, 'limit': 10})
        with self.assertRaises(ValidationError):
            self.book.query_data('res.partner', 'web_search_read', [], {
                'domain': [], 'specification': {'name': {}}, 'limit': 5001})
        with self.assertRaises(ValidationError):
            self.book.query_data('res.partner', 'unlink', [[1]], {})
        with self.assertRaises(AccessError):
            self.book.with_user(self.outsider).query_data('res.partner', 'search_count', [[]], {})
        grouped = self.book.query_data('res.partner', 'formatted_read_grouping_sets', [], {
            'domain': [], 'grouping_sets': [[], ['is_company']], 'aggregates': ['__count']})
        self.assertEqual(grouped[0][0]['__count'], sum(row['__count'] for row in grouped[1]))
