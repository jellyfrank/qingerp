from uuid import uuid4

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestKnowledgeCore(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='knowledge_manager', groups='base.group_user',
                                    company_id=cls.env.company.id, company_ids=[(6, 0, cls.env.company.ids)])
        cls.editor = new_test_user(cls.env, login='knowledge_editor', groups='base.group_user',
                                   company_id=cls.env.company.id, company_ids=[(6, 0, cls.env.company.ids)])
        cls.reader = new_test_user(cls.env, login='knowledge_reader', groups='base.group_user',
                                   company_id=cls.env.company.id, company_ids=[(6, 0, cls.env.company.ids)])
        cls.outsider = new_test_user(cls.env, login='knowledge_outsider', groups='base.group_user',
                                     company_id=cls.env.company.id, company_ids=[(6, 0, cls.env.company.ids)])
        cls.space = cls.env['qing.knowledge.space'].with_user(cls.manager).create({'name': 'SOP'})
        cls.space.with_user(cls.manager).set_member(cls.editor.id, 'editor')
        cls.space.with_user(cls.manager).set_member(cls.reader.id, 'reader')

    def _article(self):
        created = self.space.with_user(self.manager).create_article('Receiving SOP')
        article = self.env['qing.knowledge.article'].sudo().search([('uuid', '=', created['article_uuid'])])
        draft = self.env['qing.knowledge.draft'].sudo().search([('uuid', '=', created['draft_uuid'])])
        return article, draft

    def _publish(self, article, draft):
        author_draft = draft.with_user(self.manager)
        saved = author_draft.save_draft(1, 'Receiving SOP', 'Steps', '# 收货\n\nCheck SKU A-100.')
        submitted = author_draft.submit_draft(saved['draft_version'])
        return author_draft.publish_draft(submitted['draft_version'], False,
                                          submitted['content_hash'], str(uuid4()))

    def test_membership_and_published_visibility(self):
        article, draft = self._article()
        self.assertFalse(self.env['qing.knowledge.article'].with_user(self.reader).search([('id', '=', article.id)]))
        self.assertFalse(self.env['qing.knowledge.article'].with_user(self.outsider).search([('id', '=', article.id)]))
        self._publish(article, draft)
        reader_article = self.env['qing.knowledge.article'].with_user(self.reader).search([('id', '=', article.id)])
        self.assertTrue(reader_article)
        self.assertIn('A-100', reader_article.get_article()['markdown'])
        self.assertFalse(self.env['qing.knowledge.draft'].with_user(self.reader).search([('id', '=', draft.id)]))
        with self.assertRaises(AccessError):
            draft.with_user(self.reader).save_draft(draft.draft_version, 'Stolen', '', '')
        self.space.with_user(self.manager).set_member(self.reader.id, 'reader', active=False)
        self.assertFalse(self.env['qing.knowledge.article'].with_user(self.reader).search([('id', '=', article.id)]))

    def test_publish_conflict_and_idempotency(self):
        article, draft = self._article()
        first = self._publish(article, draft)
        same = draft.with_user(self.manager).publish_draft(2, False, draft.content_hash, self.env['qing.knowledge.event'].sudo().search([('draft_id', '=', draft.id)]).operation_uuid)
        self.assertEqual(first, same)
        editor_article = article.with_user(self.editor)
        editor_info = editor_article.open_draft()
        editor_draft = self.env['qing.knowledge.draft'].with_user(self.editor).search([('uuid', '=', editor_info['draft_uuid'])])
        editor_draft.save_draft(editor_info['draft_version'], 'Updated', '', '# Updated')
        editor_draft.submit_draft(editor_draft.draft_version)
        self.assertIn('+# Updated', article.with_user(self.manager).compare_submission(editor_draft.id))
        manager_info = article.with_user(self.manager).open_draft()
        manager_draft = self.env['qing.knowledge.draft'].with_user(self.manager).search([('uuid', '=', manager_info['draft_uuid'])])
        manager_draft.save_draft(manager_info['draft_version'], 'Manager update', '', '# Manager')
        manager_draft.submit_draft(manager_draft.draft_version)
        current = article.sudo().published_revision_id.uuid
        manager_draft.publish_draft(manager_draft.draft_version, current, manager_draft.content_hash, str(uuid4()))
        with self.assertRaises(ValidationError):
            editor_draft.with_user(self.manager).publish_draft(editor_draft.draft_version, current,
                                                                 editor_draft.content_hash, str(uuid4()))

    def test_revision_is_immutable(self):
        article, draft = self._article()
        self._publish(article, draft)
        revision = article.sudo().published_revision_id
        with self.assertRaises(AccessError):
            revision.sudo().write({'markdown': 'changed'})
        with self.assertRaises(AccessError):
            revision.sudo().unlink()

    def test_parent_restriction_is_immediate_for_descendants(self):
        parent, parent_draft = self._article()
        self._publish(parent, parent_draft)
        created = self.space.with_user(self.manager).create_article('Child SOP', parent.id)
        child = self.env['qing.knowledge.article'].sudo().search([('uuid', '=', created['article_uuid'])])
        draft = self.env['qing.knowledge.draft'].sudo().search([('uuid', '=', created['draft_uuid'])])
        self._publish(child, draft)
        visible = self.env['qing.knowledge.article'].with_user(self.reader)
        self.assertTrue(visible.search([('id', '=', child.id)]))
        manager_member = self.env['qing.knowledge.member'].sudo().search([
            ('space_id', '=', self.space.id), ('user_id', '=', self.manager.id),
        ])
        parent.with_user(self.manager).set_restriction([manager_member.id])
        self.assertFalse(visible.search([('id', '=', child.id)]))
        child.with_user(self.manager).move_to()
        self.assertTrue(visible.search([('id', '=', child.id)]))

    def test_move_rejects_self_descendant_missing_and_cross_space_parent(self):
        parent, _parent_draft = self._article()
        created = self.space.with_user(self.manager).create_article('Child SOP', parent.id)
        child = self.env['qing.knowledge.article'].sudo().search([('uuid', '=', created['article_uuid'])])
        other_space = self.env['qing.knowledge.space'].with_user(self.manager).create({'name': 'Other SOP'})
        other_created = other_space.with_user(self.manager).create_article('Other article')
        other_parent = self.env['qing.knowledge.article'].sudo().search([
            ('uuid', '=', other_created['article_uuid']),
        ])
        missing_id = self.env['qing.knowledge.article'].sudo().search([], order='id desc', limit=1).id + 1
        with self.assertRaises(ValidationError):
            parent.with_user(self.manager).move_to(parent.id)
        with self.assertRaises(ValidationError):
            parent.with_user(self.manager).move_to(child.id)
        with self.assertRaises(ValidationError):
            parent.with_user(self.manager).move_to(missing_id)
        with self.assertRaises(ValidationError):
            parent.with_user(self.manager).move_to(other_parent.id)
        self.assertFalse(parent.parent_id)
        self.assertEqual(child.parent_id, parent)

    def test_chinese_and_sku_search_obey_current_access(self):
        article, draft = self._article()
        self._publish(article, draft)
        search = self.env['qing.knowledge.article'].with_user(self.reader).search_articles
        self.assertEqual([row['uuid'] for row in search('收货')], [article.uuid])
        self.assertEqual([row['uuid'] for row in search('A-100')], [article.uuid])
        self.assertFalse(self.env['qing.knowledge.article'].with_user(self.outsider).search_articles('收货'))
        self.space.with_user(self.manager).set_member(self.reader.id, 'reader', active=False)
        self.assertFalse(search('收货'))
        with self.assertRaises(AccessError):
            self.env['qing.knowledge.article'].with_user(self.manager).rebuild_search_projection()
        self.assertTrue(self.env['qing.knowledge.article'].rebuild_search_projection(batch_size=100)['done'])

    def test_tags_favorites_and_history_keep_article_permissions(self):
        article, draft = self._article()
        editor_draft = draft.with_user(self.manager)
        saved = editor_draft.save_draft(1, 'Receiving SOP', '', '# 收货', ['仓库', 'SOP'])
        submitted = editor_draft.submit_draft(saved['draft_version'])
        editor_draft.publish_draft(submitted['draft_version'], False, submitted['content_hash'], str(uuid4()))
        reader_article = article.with_user(self.reader)
        self.assertEqual(set(reader_article.get_article()['tags']), {'仓库', 'SOP'})
        self.assertTrue(reader_article.toggle_favorite())
        bookmarks = self.env['qing.knowledge.bookmark'].with_user(self.reader).search([])
        self.assertEqual(bookmarks.article_id, article)
        self.assertFalse(self.env['qing.knowledge.bookmark'].with_user(self.outsider).search([]))
        self.assertEqual(len(reader_article.get_revisions()), 1)
        self.space.with_user(self.manager).set_member(self.reader.id, 'reader', active=False)
        self.assertFalse(self.env['qing.knowledge.bookmark'].with_user(self.reader).search([]))

    def test_only_manager_restores_archived_article(self):
        article, draft = self._article()
        self._publish(article, draft)
        self.space.with_user(self.manager).set_member(self.editor.id, 'publisher')
        article.with_user(self.editor).open_draft()
        article.with_user(self.manager).set_lifecycle('archived')
        with self.assertRaises(AccessError):
            article.with_user(self.editor).set_lifecycle('unpublished')
        article.with_user(self.manager).set_lifecycle('unpublished')
        self.assertEqual(article.sudo().lifecycle, 'unpublished')
