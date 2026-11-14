from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAccessRequest(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env['res.company'].create({'name': 'Access request other company'})
        cls.requester = new_test_user(cls.env, login='access_requester', groups='base.group_user',
                                      company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.other = new_test_user(cls.env, login='access_other', groups='base.group_user',
                                  company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.handler = new_test_user(cls.env, login='access_handler',
                                    groups='base.group_user,qing_base.group_access_request_handler',
                                    company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.foreign = new_test_user(cls.env, login='access_foreign',
                                    groups='base.group_user,qing_base.group_access_request_handler',
                                    company_id=cls.other_company.id, company_ids=[Command.set(cls.other_company.ids)])

    def _submit(self, **extra):
        vals = {'model_name': 'sale.order', 'operation': 'read', 'record_id': 42,
                'explanation': 'Need to review this order'}
        vals.update(extra)
        return self.env['qing.access.request'].with_user(self.requester).create(vals)

    def test_identity_and_deduplicated_notification(self):
        request = self._submit()
        same = self._submit(explanation='Another explanation')
        self.assertEqual(request, same)
        self.assertEqual(request.requester_id, self.requester)
        self.assertEqual(request.state, 'pending')
        audits = self.env['qing.audit.log'].search([
            ('model_name', '=', 'qing.access.request'), ('record_id', '=', request.id)])
        self.assertFalse(audits, 'Request details must not enter the general audit log')
        activities = self.env['mail.activity'].sudo().search([
            ('res_model', '=', request._name), ('res_id', '=', request.id)])
        handlers = request._configured_handlers(self.company)
        self.assertIn(self.handler, activities.user_id)
        self.assertNotIn(self.foreign, activities.user_id)
        self.assertEqual(set(activities.user_id.ids), set(handlers.ids))
        self.assertEqual(len(activities), len(handlers))
        self.assertTrue(all('42' not in (activity.summary or '') for activity in activities))
        with self.assertRaises(AccessError):
            self._submit(requester_id=self.other.id)

    def test_requester_and_handler_access_boundaries(self):
        request = self._submit()
        with self.assertRaises(AccessError):
            request.with_user(self.other).read(['explanation'])
        with self.assertRaises(AccessError):
            request.write({'state': 'handled'})
        with self.assertRaises(AccessError):
            request.with_user(self.foreign).write({'state': 'handled'})
        with self.assertRaises(AccessError):
            request.with_user(self.handler).write({'requester_id': self.other.id})
        request.with_user(self.handler).write({'handler_note': 'Reviewed by administrator'})
        request.with_user(self.handler).action_mark_handled()
        self.assertEqual(request.state, 'handled')
        self.assertEqual(request.handler_id, self.handler)
        self.assertEqual(request.handler_note, 'Reviewed by administrator')
        with self.assertRaises(AccessError):
            request.with_user(self.handler).action_mark_rejected()
        with self.assertRaises(AccessError):
            request.with_user(self.handler).unlink()

    def test_no_handler_is_reported_without_creating_request(self):
        self.env.ref('qing_base.group_access_request_handler').write(
            {'user_ids': [Command.clear()]})
        with self.assertRaises(UserError):
            self._submit()
        self.assertFalse(self.env['qing.access.request'].with_user(self.requester).search([]))
