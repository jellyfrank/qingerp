import base64
import io
import json
import zipfile
from uuid import uuid4

from PIL import Image
from lxml import html as lxml_html

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..services.package import parse_package
from ..services.markdown import render_markdown


@tagged('post_install', '-at_install')
class TestKnowledgePackage(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='knowledge_package_manager', groups='base.group_user',
                                    company_id=cls.env.company.id, company_ids=[(6, 0, cls.env.company.ids)])
        cls.reader = new_test_user(cls.env, login='knowledge_package_reader', groups='base.group_user',
                                   company_id=cls.env.company.id, company_ids=[(6, 0, cls.env.company.ids)])
        cls.space = cls.env['qing.knowledge.space'].with_user(cls.manager).create({'name': 'Product FAQ'})
        cls.space.with_user(cls.manager).set_member(cls.reader.id, 'reader')

    def _png(self):
        output = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(output, format='PNG')
        return output.getvalue()

    def test_asset_roundtrip_and_revocation(self):
        created = self.space.with_user(self.manager).create_article('FAQ')
        draft = self.env['qing.knowledge.draft'].with_user(self.manager).search([('uuid', '=', created['draft_uuid'])])
        uploaded = self.env['qing.knowledge.asset'].with_user(self.manager).upload_to_draft(
            draft.uuid, 'proof.png', base64.b64encode(self._png()).decode('ascii'))
        markdown = '# FAQ\n\n![proof](%s)' % uploaded['path']
        saved = draft.save_draft(draft.draft_version, 'FAQ', '', markdown)
        submitted = draft.submit_draft(saved['draft_version'])
        draft.publish_draft(submitted['draft_version'], False, submitted['content_hash'], str(uuid4()))
        article = self.env['qing.knowledge.article'].with_user(self.manager).search([('uuid', '=', created['article_uuid'])])
        exported = self.space.with_user(self.reader).export_package([article.uuid])
        specs, assets = parse_package(base64.b64decode(exported))
        self.assertEqual(specs[0]['markdown'], markdown)
        self.assertEqual(assets[uploaded['path']], self._png())
        attachment = self.env['qing.knowledge.asset'].sudo().search([
            ('revision_id', '=', article.published_revision_id.id),
        ], limit=1).attachment_id
        with self.assertRaises(AccessError):
            attachment.with_user(self.manager).write({'public': True})
        if 'attachment.share' in self.env:
            # Only upgraded databases that still load mommy_base have public shares.
            with self.assertRaises(AccessError):
                self.env['attachment.share'].with_user(self.manager).create({'attachment_id': attachment.id})
        result = self.space.with_user(self.manager).import_package(exported, 'repeat-import-1')
        self.assertEqual(result, self.space.with_user(self.manager).import_package(exported, 'repeat-import-1'))
        self.assertNotEqual(result[0]['article_uuid'], article.uuid)
        self.space.with_user(self.manager).set_member(self.reader.id, 'reader', active=False)
        with self.assertRaises(AccessError):
            self.space.with_user(self.reader).export_package([article.uuid])

    def test_package_roundtrip_rebuilds_only_internal_parent_links(self):
        parent_created = self.space.with_user(self.manager).create_article('Parent')
        parent = self.env['qing.knowledge.article'].with_user(self.manager).search([
            ('uuid', '=', parent_created['article_uuid']),
        ])
        child_created = self.space.with_user(self.manager).create_article('Child')
        child = self.env['qing.knowledge.article'].with_user(self.manager).search([
            ('uuid', '=', child_created['article_uuid']),
        ])
        for created in (parent_created, child_created):
            draft = self.env['qing.knowledge.draft'].with_user(self.manager).search([
                ('uuid', '=', created['draft_uuid']),
            ])
            submitted = draft.submit_draft(draft.draft_version)
            draft.publish_draft(submitted['draft_version'], False, submitted['content_hash'], str(uuid4()))
        child.move_to(parent.id)
        exported = self.space.with_user(self.manager).export_package([parent.uuid, child.uuid])
        imported = self.space.with_user(self.manager).import_package(exported, str(uuid4()))
        by_path = {item['path']: item['article_uuid'] for item in imported}
        new_parent = self.env['qing.knowledge.article'].sudo().search([
            ('uuid', '=', by_path[f'articles/{parent.uuid}.md']),
        ])
        new_child = self.env['qing.knowledge.article'].sudo().search([
            ('uuid', '=', by_path[f'articles/{child.uuid}.md']),
        ])
        self.assertNotEqual(new_parent.id, parent.id)
        self.assertEqual(new_child.parent_id, new_parent)

    def test_package_rejects_cyclic_parent_links_before_writes(self):
        first, second = str(uuid4()), str(uuid4())
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as package:
            package.writestr('first.md', '# First')
            package.writestr('second.md', '# Second')
            package.writestr('manifest.json', json.dumps({
                'schema': 'qing-knowledge-package/1',
                'articles': [
                    {'path': 'first.md', 'article_uuid': first, 'parent_uuid': second},
                    {'path': 'second.md', 'article_uuid': second, 'parent_uuid': first},
                ],
            }))
        before = self.env['qing.knowledge.article'].sudo().search_count([('space_id', '=', self.space.id)])
        with self.assertRaises(ValidationError):
            self.space.with_user(self.manager).import_package(base64.b64encode(raw.getvalue()), str(uuid4()))
        self.assertEqual(self.env['qing.knowledge.article'].sudo().search_count([
            ('space_id', '=', self.space.id),
        ]), before)

    def test_zip_preflight_blocks_traversal_and_bomb(self):
        for name in ('../secret.md', '/secret.md', 'articles/../../secret.md'):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as package:
                package.writestr(name, '# secret')
            with self.assertRaises(ValidationError):
                parse_package(output.getvalue())
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
            package.writestr('huge.md', 'x' * (1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            parse_package(output.getvalue())

    def test_plain_markdown_import_is_draft(self):
        result = self.space.with_user(self.manager).import_markdown('Procedure', '# Procedure\n步骤一', 'md-import-1')
        self.assertEqual(result, self.space.with_user(self.manager).import_markdown(
            'Procedure', '# Procedure\n步骤一', 'md-import-1'))
        article = self.env['qing.knowledge.article'].with_user(self.manager).search([('uuid', '=', result[0]['article_uuid'])])
        self.assertEqual(article.lifecycle, 'unpublished')
        self.assertFalse(self.env['qing.knowledge.article'].with_user(self.reader).search([('id', '=', article.id)]))

    def test_unsupported_relative_image_fails_without_creating_a_draft(self):
        before = self.env['qing.knowledge.article'].sudo().search_count([('space_id', '=', self.space.id)])
        with self.assertRaises(ValidationError):
            self.space.with_user(self.manager).import_markdown(
                'Broken image', '![diagram](images/diagram.png)', 'bad-md-image-1')
        self.assertEqual(self.env['qing.knowledge.article'].sudo().search_count([
            ('space_id', '=', self.space.id)]), before)

    def test_markdown_does_not_execute_html_or_external_images(self):
        html = render_markdown('<script>alert(1)</script>\n\n![remote](https://example.com/track.png)\n\n[x](javascript:alert(1))')
        self.assertNotIn('<script', html)
        self.assertNotIn('track.png', html)
        self.assertNotIn('href="javascript:', html)

    def test_editor_breaks_and_table_structure(self):
        source = '# 标题\n\n<br />\n\n| 1 | 1 | 1 |\n| :--- | :---: | ---: |\n| 订单<br />明细 | 订单 | 订单 |\n| | | |\n'
        document = lxml_html.fragment_fromstring(render_markdown(source), create_parent='div')
        self.assertEqual(len(document.xpath('.//br')), 2)
        self.assertNotIn('<br />', document.text_content())
        self.assertEqual(len(document.xpath('.//table/thead/tr/th')), 3)
        self.assertEqual(len(document.xpath('.//table/tbody/tr')), 2)
        self.assertEqual(document.xpath('.//table/tbody/tr[1]/td[1]/br')[0].tail, '\n明细')
        for tag in ('<br>', '<br/>', '<br />', '<BR />'):
            with self.subTest(tag=tag):
                self.assertIn('<br', render_markdown('before%safter' % tag))

    def test_break_examples_remain_literal_and_raw_html_stays_disabled(self):
        source = '`<br />`\n\n```html\n<br />\n```\n\n\\<br />\n\n&lt;br /&gt;'
        document = lxml_html.fragment_fromstring(render_markdown(source), create_parent='div')
        self.assertFalse(document.xpath('.//br'))
        self.assertEqual(document.text_content().count('<br />'), 4)
        unsafe = '<br onmouseover="alert(1)">\n<br style="display:none">\n<img src="https://example.com/pixel">\n<script>alert(1)</script>'
        document = lxml_html.fragment_fromstring(render_markdown(unsafe), create_parent='div')
        self.assertFalse(document.xpath('.//br | .//img | .//script | .//*[@onmouseover]'))
