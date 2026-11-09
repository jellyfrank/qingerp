import base64
from uuid import uuid4

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestKnowledgeWorkspace(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='workspace_manager', groups='base.group_user')
        cls.editor = new_test_user(cls.env, login='workspace_editor', groups='base.group_user')
        cls.reader = new_test_user(cls.env, login='workspace_reader', groups='base.group_user')
        cls.space = cls.env['qing.knowledge.space'].with_user(cls.manager).create({'name': 'Workspace'})
        cls.space.with_user(cls.manager).set_member(cls.editor.id, 'editor')
        cls.space.with_user(cls.manager).set_member(cls.reader.id, 'reader')

    def _draft(self, author, space=None, title='验收说明'):
        info = (space or self.space).with_user(author).create_article(title)
        draft = self.env['qing.knowledge.draft'].with_user(author).search([('uuid', '=', info['draft_uuid'])])
        saved = draft.save_draft(draft.draft_version, title, '摘要', '## 收货\n\n先核对序列号，再验证入库。')
        return draft, saved

    def test_review_default_and_direct_permission_boundary(self):
        self.assertEqual(self.space.publication_policy, 'review')
        draft, saved = self._draft(self.manager)
        with self.assertRaises(AccessError):
            draft.publish_direct(saved['draft_version'], False, saved['content_hash'], str(uuid4()))
        self.space.with_user(self.manager).write({'publication_policy': 'direct'})
        editor_draft, editor_saved = self._draft(self.editor)
        with self.assertRaises(AccessError):
            editor_draft.publish_direct(editor_saved['draft_version'], False, editor_saved['content_hash'], str(uuid4()))
        with self.assertRaises(AccessError):
            self.space.with_user(self.editor).write({'publication_policy': 'direct'})
        self.assertEqual(editor_draft.state, 'editing')

    def test_direct_publish_is_idempotent_and_reader_sees_stable_revision(self):
        self.space.with_user(self.manager).write({'publication_policy': 'direct'})
        draft, saved = self._draft(self.manager)
        operation = str(uuid4())
        first = draft.publish_direct(saved['draft_version'], False, saved['content_hash'], operation)
        self.assertEqual(first, draft.publish_direct(saved['draft_version'], False, saved['content_hash'], operation))
        self.assertEqual(self.env['qing.knowledge.event'].sudo().search_count([('operation_uuid', '=', operation)]), 1)
        article = draft.article_id
        reader_article = article.with_user(self.reader)
        published_source = reader_article.get_article()['markdown']
        info = article.open_draft()
        draft.invalidate_recordset()
        draft.save_draft(info['draft_version'], '新的草稿', '', '尚未发布的修改')
        self.assertEqual(reader_article.get_article()['markdown'], published_source)
        self.assertFalse(self.env['qing.knowledge.draft'].with_user(self.reader).list_my_drafts())

    def test_direct_publish_rejects_stale_content_version_and_revoked_member(self):
        self.space.with_user(self.manager).write({'publication_policy': 'direct'})
        draft, saved = self._draft(self.manager)
        with self.assertRaises(ValidationError):
            draft.publish_direct(saved['draft_version'] - 1, False, saved['content_hash'], str(uuid4()))
        with self.assertRaises(ValidationError):
            draft.publish_direct(saved['draft_version'], False, 'changed-content', str(uuid4()))
        publisher = new_test_user(self.env, login='workspace_publisher', groups='base.group_user')
        self.space.with_user(self.manager).set_member(publisher.id, 'publisher')
        own, version = self._draft(publisher)
        self.space.with_user(self.manager).set_member(publisher.id, 'publisher', active=False)
        with self.assertRaises(AccessError):
            own.publish_direct(version['draft_version'], False, version['content_hash'], str(uuid4()))

    def test_draft_search_is_personal_permission_filtered_and_cross_space(self):
        own, _ = self._draft(self.manager)
        other, _ = self._draft(self.editor)
        second = self.env['qing.knowledge.space'].with_user(self.manager).create({'name': 'Other space'})
        second_own, _ = self._draft(self.manager, second)
        Draft = self.env['qing.knowledge.draft'].with_user(self.manager)
        results = Draft.list_my_drafts('序列号')
        self.assertEqual({item['id'] for item in results}, {own.id, second_own.id})
        self.assertNotIn(other.id, {item['id'] for item in results})
        self.assertEqual({item['id'] for item in Draft.list_my_drafts('', self.space.uuid)}, {own.id})
        editor_search = self.env['qing.knowledge.draft'].with_user(self.editor)
        self.assertEqual({item['id'] for item in editor_search.list_my_drafts()}, {other.id})
        self.space.with_user(self.manager).set_member(self.editor.id, 'editor', active=False)
        self.assertFalse(editor_search.list_my_drafts())

    def test_direct_publish_keeps_review_space_and_current_revision_check(self):
        self.space.with_user(self.manager).write({'publication_policy': 'direct'})
        draft, saved = self._draft(self.manager)
        draft.publish_direct(saved['draft_version'], False, saved['content_hash'], str(uuid4()))
        info = draft.article_id.open_draft()
        draft.invalidate_recordset()
        changed = draft.save_draft(info['draft_version'], '下一版', '', '第二版内容')
        with self.assertRaises(ValidationError):
            draft.publish_direct(changed['draft_version'], False, changed['content_hash'], str(uuid4()))
        self.space.with_user(self.manager).write({'publication_policy': 'review'})
        with self.assertRaises(AccessError):
            draft.publish_direct(changed['draft_version'], info['base_revision_uuid'], changed['content_hash'], str(uuid4()))

    def test_review_cannot_overwrite_a_newer_published_base(self):
        self.space.with_user(self.manager).write({'publication_policy': 'direct'})
        draft, saved = self._draft(self.manager)
        first = draft.publish_direct(saved['draft_version'], False, saved['content_hash'], str(uuid4()))
        editor_info = draft.article_id.with_user(self.editor).open_draft()
        editor_draft = self.env['qing.knowledge.draft'].with_user(self.editor).search([('uuid', '=', editor_info['draft_uuid'])])
        editor_saved = editor_draft.save_draft(editor_info['draft_version'], 'Editor changes', '', 'Based on version one')
        submission = editor_draft.submit_draft(editor_saved['draft_version'])
        manager_info = draft.article_id.open_draft()
        draft.invalidate_recordset()
        manager_saved = draft.save_draft(manager_info['draft_version'], 'Manager changes', '', 'Version two')
        second = draft.publish_direct(manager_saved['draft_version'], first['revision_uuid'], manager_saved['content_hash'], str(uuid4()))
        with self.assertRaises(ValidationError):
            editor_draft.with_user(self.manager).publish_draft(submission['draft_version'], second['revision_uuid'],
                                                              submission['content_hash'], str(uuid4()))
        self.assertEqual(draft.article_id.published_revision_id.uuid, second['revision_uuid'])

    def test_corrupt_image_is_rejected_without_creating_asset(self):
        draft, _saved = self._draft(self.manager)
        # A PNG with an invalid IDAT checksum must fail the actual image verification.
        corrupt = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3iQAAAAASUVORK5CYII='
        self.assertTrue(base64.b64decode(corrupt).startswith(b'\x89PNG'))
        with self.assertRaises(ValidationError):
            self.env['qing.knowledge.asset'].with_user(self.manager).upload_to_draft(
                draft.uuid, 'corrupt.png', corrupt)
        self.assertFalse(draft.asset_ids)

    def test_trash_hides_personal_drafts_and_restore_preserves_content(self):
        draft, _saved = self._draft(self.manager)
        article = draft.article_id.with_user(self.manager)
        source = draft.markdown
        Draft = self.env['qing.knowledge.draft'].with_user(self.manager)
        self.assertIn(draft.id, {item['id'] for item in Draft.list_my_drafts()})
        article.set_lifecycle('trashed')
        self.assertNotIn(draft.id, {item['id'] for item in Draft.list_my_drafts()})
        self.assertFalse(article.get_article()['can_edit'])
        self.assertTrue(article.get_article()['can_manage'])
        article.set_lifecycle('unpublished')
        self.assertEqual(article.lifecycle, 'archived')
        self.assertIn(draft.id, {item['id'] for item in Draft.list_my_drafts()})
        self.assertEqual(draft.markdown, source)

    def test_trash_keeps_revision_and_manager_restore_permission(self):
        self.space.with_user(self.manager).write({'publication_policy': 'direct'})
        draft, saved = self._draft(self.manager)
        draft.publish_direct(saved['draft_version'], False, saved['content_hash'], str(uuid4()))
        article = draft.article_id.with_user(self.manager)
        revision = article.published_revision_id
        with self.assertRaises(AccessError):
            article.with_user(self.editor).set_lifecycle('trashed')
        article.set_lifecycle('trashed')
        self.assertEqual(article.published_revision_id, revision)
        self.space.with_user(self.manager).set_member(self.reader.id, 'manager')
        self.assertEqual(article.with_user(self.reader).name, revision.title)
        with self.assertRaises(AccessError):
            article.with_user(self.editor).set_lifecycle('unpublished')
        article.set_lifecycle('unpublished')
        self.assertEqual(article.published_revision_id, revision)
        self.assertTrue(article.get_article()['can_edit'])
