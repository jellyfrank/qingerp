from odoo.tests import TransactionCase, tagged

from odoo.addons.qing_base.models.qing_brand_patch import BRAND_PATCHES, MAIL_TEMPLATE_FIELDS, rebrand_mail_text


@tagged('post_install', '-at_install')
class TestQingBrandPatch(TransactionCase):

    def test_patches_follow_their_parent_templates(self):
        self.env['qing.brand.patch']._sync()
        for key, (parent_xmlid, _arch) in BRAND_PATCHES.items():
            parent = self.env.ref(parent_xmlid, raise_if_not_found=False)
            patch = self.env.ref('qing_base.brand_patch_%s' % key, raise_if_not_found=False)
            if not parent:
                self.assertFalse(patch, key)
                continue
            self.assertTrue(patch, key)
            self.assertEqual(patch.inherit_id, parent, key)
            for lang, _label in self.env['res.lang'].get_installed():
                combined = parent.with_context(lang=lang).get_combined_arch()
                self.assertNotIn('odoo.com', combined, (key, lang))
                self.assertNotIn('Odoo', combined, (key, lang))

    def test_sync_is_idempotent(self):
        patches = self.env['qing.brand.patch']
        patches._sync()
        self.assertEqual(patches._sync(), [])

    def test_patches_survive_the_upgrade_cleanup(self):
        self.env['qing.brand.patch']._sync()
        records = self.env['ir.model.data'].search([('module', '=', 'qing_base'), ('name', '=like', 'brand_patch_%')])
        self.assertTrue(all(records.mapped('noupdate')))

    def test_patch_terms_get_chinese(self):
        if not self.env.ref('purchase.portal_my_purchase_order', raise_if_not_found=False):
            self.skipTest('Needs Purchase.')
        self.env['res.lang']._activate_lang('zh_CN')
        self.env['qing.brand.patch']._sync()
        self.env['ir.module.module']._load_module_terms(['qing_base'], ['zh_CN'])
        patch = self.env.ref('qing_base.brand_patch_purchase_portal_connect')
        self.assertNotIn('Connect your software', patch.with_context(lang='zh_CN').arch_db)

    def test_a_patch_that_no_longer_applies_is_skipped(self):
        parent = self.env.ref('purchase.portal_my_purchase_order', raise_if_not_found=False)
        if not parent:
            self.skipTest('Needs Purchase.')
        self.env['qing.brand.patch']._sync()
        patch = self.env.ref('qing_base.brand_patch_purchase_portal_connect')
        patch.unlink()
        parent.with_context(lang=None).arch_db = parent.with_context(lang=None).arch_db.replace(
            'portal_connect_software_modal', 'portal_connect_other_modal')
        self.assertNotIn('purchase_portal_connect', self.env['qing.brand.patch']._sync())
        self.assertFalse(self.env.ref('qing_base.brand_patch_purchase_portal_connect', raise_if_not_found=False))
        self.assertIn('portal_connect_other_modal', parent.get_combined_arch())

    def test_mail_text(self):
        cases = {
            'invites you to connect to Odoo': 'invites you to connect to QingERP',
            'Enjoy Odoo!<br/>': 'Enjoy QingERP!<br/>',
            '您的Odoo帐户': '您的QingERP帐户',
            'uses <a href="https://odoo.com" t-attf-style="color: {{x}};">Odoo</a> to run': 'uses QingERP to run',
            '<td><img alt="Odoo" src="/web/static/img/logo_inverse_white_206px.png" style="height:30px;"/></td>': '<td></td>',
            '<t t-if="x">Odoo Discuss</t>': '<t t-if="x">QingERP Discuss</t>',
            'It runs on Odoo.': 'It runs on QingERP.',
            # Left alone: the assistant's name, addresses, paths, expressions.
            '<t t-out="x">OdooBot</t>': '<t t-out="x">OdooBot</t>',
            'http://yourcompany.odoo.com': 'http://yourcompany.odoo.com',
            'see Odoo.com': 'see Odoo.com',
            '<a t-attf-href="/odoo/accounting/{{ object.id }}" title="Odoo">open</a>': '<a t-attf-href="/odoo/accounting/{{ object.id }}" title="Odoo">open</a>',
            "{{ 'Odoo' }} invites you": "{{ 'Odoo' }} invites you",
        }
        for text, expected in cases.items():
            self.assertEqual(rebrand_mail_text(text), expected)

    def test_mail_templates_name_qingerp(self):
        self.env['qing.brand.patch']._sync()
        for xmlid, field_names in MAIL_TEMPLATE_FIELDS.items():
            template = self.env.ref(xmlid, raise_if_not_found=False)
            if not template:
                continue
            for lang in ['en_US'] + [code for code, _label in self.env['res.lang'].get_installed()]:
                for field_name in field_names:
                    value = str(template.with_context(lang=lang)[field_name] or '')
                    self.assertEqual(rebrand_mail_text(value), value, (xmlid, field_name, lang))

    def test_edited_invitation_mail_keeps_its_text(self):
        template = self.env.ref('auth_signup.set_password_email').with_context(lang='en_US')
        template.body_html = '<p>Welcome aboard, <a href="https://odoo.com" style="color: red;">Odoo</a> is our system.</p>'
        self.env['qing.brand.patch']._sync()
        self.assertIn('Welcome aboard, QingERP is our system.', template.body_html)
