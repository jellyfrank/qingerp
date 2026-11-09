from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPartnerApiUid(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        cls.Partners = cls.env['res.partner'].with_user(cls.admin).sudo(False)
        cls.Users = cls.env['res.users'].with_user(cls.admin).sudo(False).with_context(
            no_reset_password=True, tracking_disable=True,
        )

    def _create_user(self, login):
        return self.Users.create({'name': login, 'login': login})

    def test_user_defaults_do_not_supply_contact_api_uid(self):
        self.assertFalse(self.Users.env.su)
        self.assertTrue(self.admin.has_group('base.group_system'))
        defaults = self.Users._add_missing_default_values({
            'name': 'UID default test', 'login': 'uid-default-test',
        })
        self.assertNotIn('qing_api_uid', defaults)

    def test_administrator_can_create_user(self):
        user = self._create_user('uid-create-test')
        self.assertTrue(user.partner_id.qing_api_uid)
        self.assertEqual(len(user.partner_id.qing_api_uid), 32)

    def test_administrator_can_copy_user_with_new_contact_uid(self):
        user = self._create_user('uid-copy-source')
        original_uid = user.partner_id.qing_api_uid
        duplicate = user.copy({'login': 'uid-copy-target'})
        self.assertNotEqual(user.partner_id, duplicate.partner_id)
        self.assertTrue(duplicate.partner_id.qing_api_uid)
        self.assertNotEqual(original_uid, duplicate.partner_id.qing_api_uid)
        self.assertEqual(user.partner_id.qing_api_uid, original_uid)

    def test_administrator_can_create_portal_user(self):
        user = self.Users.create({
            'name': 'UID portal test', 'login': 'uid-portal-test',
            'group_ids': [Command.set(self.env.ref('base.group_portal').ids)],
        })
        self.assertTrue(user.share)
        self.assertTrue(user.partner_id.qing_api_uid)

    def test_contact_batch_generates_distinct_uids_without_mutating_input(self):
        values = [{'name': 'UID contact A'}, {'name': 'UID contact B'}]
        contacts = self.Partners.create(values)
        uids = contacts.mapped('qing_api_uid')
        self.assertEqual(len(set(uids)), 2)
        self.assertTrue(all(len(uid) == 32 for uid in uids))
        self.assertTrue(all('qing_api_uid' not in value for value in values))

    def test_contact_copy_generates_new_uid(self):
        contact = self.Partners.create({'name': 'UID contact copy'})
        duplicate = contact.copy()
        self.assertTrue(duplicate.qing_api_uid)
        self.assertNotEqual(contact.qing_api_uid, duplicate.qing_api_uid)

    def test_administrator_cannot_supply_uid_on_contact_or_user_creation(self):
        for Model, values in [
            (self.Partners, {'name': 'UID forged contact'}),
            (self.Users, {'name': 'UID forged user', 'login': 'uid-forged-user'}),
        ]:
            with self.assertRaises(AccessError), self.cr.savepoint():
                Model.create(dict(values, qing_api_uid='f' * 32))

    def test_existing_uid_remains_immutable_through_contact_and_user(self):
        user = self._create_user('uid-immutable-test')
        original_uid = user.partner_id.qing_api_uid
        for record in (user.partner_id, user):
            for value in (original_uid, 'f' * 32, False):
                with self.assertRaises(AccessError), self.cr.savepoint():
                    record.write({'qing_api_uid': value})
        self.assertEqual(user.partner_id.qing_api_uid, original_uid)

    def test_contact_context_cannot_choose_generated_uid(self):
        forged_uid = 'f' * 32
        contact = self.Partners.with_context(default_qing_api_uid=forged_uid).create({
            'name': 'UID protected default',
        })
        self.assertTrue(contact.qing_api_uid)
        self.assertNotEqual(contact.qing_api_uid, forged_uid)
