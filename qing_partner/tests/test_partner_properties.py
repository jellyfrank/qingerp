from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import Form, TransactionCase, new_test_user, tagged
from odoo.addons.qing_base.tools import strict_computes


@tagged('post_install', '-at_install')
class TestPartnerProperties(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.editor = new_test_user(cls.env, login='properties_editor', groups='qing_partner.group_partner_editor')
        cls.reader = new_test_user(cls.env, login='properties_reader', groups='base.group_user')
        cls.manager = new_test_user(cls.env, login='properties_manager', groups='qing_partner.group_partner_manager')
        cls.portal = new_test_user(cls.env, login='properties_portal', groups='base.group_portal')
        cls.definition = cls.env['properties.base.definition']._get_definition_for_property_field('res.partner', 'properties')
        cls.other_definition = cls.env['properties.base.definition']._get_definition_for_property_field('res.users', 'properties')
        cls.permission = cls.env.ref('qing_partner.properties_partner_internal_read')
        cls.specification = {
            'name': {}, 'properties': {},
            'properties_base_definition_id': {'fields': {'display_name': {}}},
        }

    def test_internal_read_is_contact_field_scoped(self):
        for user in (self.editor, self.reader, self.manager):
            with self.subTest(user=user.login):
                definition = self.definition.with_user(user)
                self.assertFalse(definition.env.su)
                self.assertTrue(definition.has_access('read'))
                self.assertEqual(definition.read(['properties_definition'])[0]['id'], self.definition.id)
                with self.assertRaises(AccessError):
                    self.other_definition.with_user(user).read(['properties_definition'])

    def test_global_read_restriction_does_not_grant_permission(self):
        self.permission.active = False
        self.env['ir.access'].create({
            'name': 'Properties global read restriction',
            'model_id': self.env['ir.model']._get('properties.base.definition').id,
            'operation': 'r',
        })
        self.assertFalse(self.definition.with_user(self.editor).has_access('read'))
        with self.assertRaises(AccessError):
            self.env['res.partner'].with_user(self.editor).onchange({}, [], self.specification)
        self.permission.active = True
        result = self.env['res.partner'].with_user(self.editor).onchange({}, [], self.specification)
        self.assertEqual(result['value']['properties_base_definition_id']['id'], self.definition.id)

    def test_new_contact_onchange_keeps_native_properties(self):
        for user in (self.editor, self.manager):
            with self.subTest(user=user.login):
                result = self.env['res.partner'].with_user(user).onchange({}, [], self.specification)
                self.assertEqual(result['value']['properties_base_definition_id']['id'], self.definition.id)
                self.assertIn('properties', result['value'])
        with self.assertRaises(AccessError):
            self.env['res.partner'].with_user(self.reader).onchange({}, [], self.specification)

    def test_editor_contact_creation_and_readback(self):
        Partners = self.env['res.partner'].with_user(self.editor)
        partner = Partners.create({'name': 'Properties contact creation'})
        values = partner.web_read(self.specification)[0]
        self.assertEqual(values['name'], 'Properties contact creation')
        self.assertEqual(values['properties_base_definition_id']['id'], self.definition.id)
        self.assertEqual(partner.qing_api_uid, partner.sudo().qing_api_uid)
        partner.write({'phone': '123456'})
        self.assertEqual(partner.phone, '123456')

    def test_editor_native_new_contact_form_save(self):
        with strict_computes(), Form(self.env['res.partner'].with_user(self.editor)) as form:
            form.name = 'Native contact form save'
            form.phone = '123456'
        self.assertEqual(form.record.name, 'Native contact form save')
        self.assertEqual(form.record.phone, '123456')

    def test_creation_accepts_unchanged_protected_defaults(self):
        partner = self.env['res.partner'].with_user(self.editor).create({
            'name': 'Native empty defaults', 'vat': False, 'properties': [],
            'category_id': [Command.set([])], 'child_ids': [Command.clear()],
        })
        self.assertFalse(partner.category_id)
        self.assertFalse(partner.child_ids)
        with self.assertRaises(AccessError):
            partner.write({'vat': False})

    def test_creation_rejects_protected_overrides_and_context(self):
        Partners = self.env['res.partner'].with_user(self.editor)
        with self.assertRaises(AccessError):
            Partners.create({'name': 'Protected VAT', 'vat': '123456789'})
        with self.assertRaises(AccessError):
            Partners.with_context(default_vat='123456789').create({'name': 'Protected default'})
        with self.assertRaises(AccessError):
            Partners.create({'name': 'Protected identity', 'user_id': False})

    def test_creation_rejects_relational_commands_ending_at_default(self):
        category = self.env['res.partner.category'].create({'name': 'Unchanged protected category'})
        Partners = self.env['res.partner'].with_user(self.editor)
        for command in (
            Command.update(category.id, {'name': 'Injected category update'}),
            Command.delete(category.id), Command.unlink(category.id),
            Command.create({'name': 'Injected category creation'}),
        ):
            with self.subTest(command=command[0]), self.assertRaises(AccessError):
                Partners.create({'name': 'Rejected relation', 'category_id': [command, Command.clear()]})
        self.assertTrue(category.exists())
        self.assertEqual(category.name, 'Unchanged protected category')

    def test_internal_users_cannot_manage_definitions(self):
        for user in (self.editor, self.reader, self.manager):
            definition = self.definition.with_user(user)
            self.assertFalse(definition.has_access('write'))
            self.assertFalse(definition.has_access('unlink'))
            self.assertFalse(definition.browse().has_access('create'))
            with self.assertRaises(AccessError):
                definition.write({'properties_definition': []})
            with self.assertRaises(AccessError):
                definition.unlink()
            with self.assertRaises(AccessError):
                definition.create({'properties_field_id': self.definition.properties_field_id.id})

    def test_portal_and_public_keep_native_boundary(self):
        for user in (self.portal, self.env.ref('base.public_user')):
            self.assertFalse(self.definition.with_user(user).has_access('read'))
            with self.assertRaises(AccessError):
                self.definition.with_user(user).read(['properties_definition'])

    def test_system_administrator_keeps_definition_management(self):
        definition = self.definition.with_user(self.env.ref('base.user_admin')).sudo(False)
        self.assertTrue(definition.has_access('read'))
        self.assertTrue(definition.has_access('write'))
        definition.write({'properties_definition': []})

    def test_permission_xmlid_is_upgrade_stable(self):
        self.assertEqual(self.permission.operation, 'r')
        self.assertEqual(self.permission.group_id, self.env.ref('base.group_user'))
        self.assertEqual(self.permission.model_id.model, 'properties.base.definition')
        self.assertIn(str(self.env.ref('base.field_res_partner__properties').id), self.permission.domain)
