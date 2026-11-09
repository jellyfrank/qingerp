import base64
import io
from uuid import uuid4

from PIL import Image

from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestKnowledgeHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.author = new_test_user(cls.env, login='knowledge_http_author', password='author-test-only',
                                   groups='base.group_user', company_id=cls.env.company.id,
                                   company_ids=[(6, 0, cls.env.company.ids)])
        cls.reader = new_test_user(cls.env, login='knowledge_http_reader', password='reader-test-only',
                                   groups='base.group_user', company_id=cls.env.company.id,
                                   company_ids=[(6, 0, cls.env.company.ids)])
        cls.space = cls.env['qing.knowledge.space'].with_user(cls.author).create({'name': 'HTTP assets'})
        cls.space.with_user(cls.author).set_member(cls.reader.id, 'reader')
        info = cls.space.with_user(cls.author).create_article('Illustrated FAQ')
        cls.draft = cls.env['qing.knowledge.draft'].with_user(cls.author).search([('uuid', '=', info['draft_uuid'])])
        output = io.BytesIO()
        Image.new('RGB', (1, 1), 'blue').save(output, 'PNG')
        cls.raw = output.getvalue()
        cls.asset = cls.env['qing.knowledge.asset'].with_user(cls.author).upload_to_draft(
            cls.draft.uuid, 'blue.png', base64.b64encode(cls.raw).decode('ascii'))
        saved = cls.draft.save_draft(cls.draft.draft_version, 'Illustrated FAQ', '',
                                     '![blue](%s)' % cls.asset['path'])
        submitted = cls.draft.submit_draft(saved['draft_version'])
        published = cls.draft.publish_draft(submitted['draft_version'], False,
                                            submitted['content_hash'], str(uuid4()))
        cls.revision_uuid = published['revision_uuid']
        cls.attachment_id = cls.env['qing.knowledge.asset'].sudo().search([
            ('revision_id.uuid', '=', cls.revision_uuid),
        ], limit=1).attachment_id.id

    def test_revision_asset_requires_live_membership_on_all_routes(self):
        path = '/qing/knowledge/asset/%s/%s' % (self.revision_uuid, self.asset['path'])
        native = '/web/content/%s?download=1' % self.attachment_id
        image = '/web/image/%s' % self.attachment_id
        self.assertNotEqual(self.url_open(path).headers.get('Content-Type'), 'image/png')
        self.assertNotEqual(self.url_open(native).headers.get('Content-Type'), 'image/png')
        self.assertNotEqual(self.url_open(image).content, self.raw)
        self.authenticate(self.reader.login, 'reader-test-only')
        self.assertEqual(self.url_open(path).status_code, 200)
        self.assertEqual(self.url_open(native).status_code, 200)
        self.assertEqual(self.url_open(image).content, self.raw)
        self.space.with_user(self.author).set_member(self.reader.id, 'reader', active=False)
        self.assertNotEqual(self.url_open(path).headers.get('Content-Type'), 'image/png')
        self.assertNotEqual(self.url_open(native).headers.get('Content-Type'), 'image/png')
        self.assertNotEqual(self.url_open(image).content, self.raw)

    def test_pdf_preview_download_and_live_permission(self):
        self.authenticate(self.author.login, 'author-test-only')
        info = self.draft.article_id.with_user(self.author).open_draft()
        draft = self.env['qing.knowledge.draft'].with_user(self.author).search([('uuid', '=', info['draft_uuid'])])
        asset = self.env['qing.knowledge.asset'].with_user(self.author).upload_to_draft(
            draft.uuid, 'controlled.pdf', base64.b64encode(b'%PDF-1.4\n%%EOF'))
        saved = draft.save_draft(draft.draft_version, 'PDF notes', '', '[PDF](%s)' % asset['path'])
        draft_path = '/qing/knowledge/draft_asset/%s/%s' % (draft.uuid, asset['path'])
        response = self.url_open(draft_path)
        self.assertEqual(response.headers.get('Content-Type'), 'application/pdf')
        self.assertNotIn('attachment', response.headers.get('Content-Disposition', ''))
        self.assertIn('attachment', self.url_open(draft_path + '?download=1').headers.get('Content-Disposition', ''))
        submitted = draft.submit_draft(saved['draft_version'])
        result = draft.publish_draft(submitted['draft_version'], info['base_revision_uuid'],
                                     submitted['content_hash'], str(uuid4()))
        path = '/qing/knowledge/asset/%s/%s' % (result['revision_uuid'], asset['path'])
        self.authenticate(self.reader.login, 'reader-test-only')
        response = self.url_open(path)
        self.assertEqual(response.headers.get('Content-Type'), 'application/pdf')
        self.assertEqual(response.headers.get('Cache-Control'), 'private, no-store')
        self.assertNotEqual(self.url_open(draft_path).headers.get('Content-Type'), 'application/pdf')
        self.space.with_user(self.author).set_member(self.reader.id, 'reader', active=False)
        self.assertNotEqual(self.url_open(path).headers.get('Content-Type'), 'application/pdf')
