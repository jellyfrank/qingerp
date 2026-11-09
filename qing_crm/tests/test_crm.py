from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingCrm(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login='crm_manager',
            groups='sales_team.group_sale_manager,qing_partner.group_partner_manager')
        cls.salesperson = new_test_user(
            cls.env, login='crm_salesperson', groups='sales_team.group_sale_salesman')
        cls.other_salesperson = new_test_user(
            cls.env, login='crm_other_salesperson', groups='sales_team.group_sale_salesman')
        (cls.salesperson | cls.other_salesperson).write({
            'qing_partner_access_mode': 'restricted',
            'qing_customer_scope': 'own',
            'qing_supplier_scope': 'none',
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Prospective Customer', 'is_company': True})

    def _enroll_public(self):
        wizard = self.env['qing.crm.enroll.wizard'].with_user(self.manager).create({
            'company_id': self.env.company.id,
            'destination': 'public',
            'partner_id': self.partner.id,
            'reason': 'Initial prospect import',
        })
        wizard.action_confirm()
        return self.env['qing.partner.role'].sudo().search([
            ('partner_id', '=', self.partner.id), ('company_id', '=', self.env.company.id),
            ('role', '=', 'customer'),
        ])

    def test_public_pool_claim_and_second_claim_rejected(self):
        role = self._enroll_public()
        self.assertEqual(role.state, 'pending')
        self.assertEqual(role.pool_state, 'public')
        self.assertFalse(role.effective_owner_id)
        entry = self.env['qing.crm.pool.entry'].with_user(self.salesperson).search([])
        self.assertEqual(len(entry), 1)
        entry.action_claim()
        self.assertEqual(role.effective_owner_id, self.salesperson)
        self.assertEqual(role.pool_state, 'private')
        action = role.action_open_customer()
        self.assertEqual(action['views'], [(self.env.ref('qing_crm.view_crm_customer_form').id, 'form')])
        with self.assertRaises(UserError):
            entry.with_user(self.other_salesperson).action_claim()

    def test_followup_is_structured_and_immutable(self):
        role = self._enroll_public()
        entry = self.env['qing.crm.pool.entry'].with_user(self.salesperson).search([])
        entry.action_claim()
        foreign_attachment = self.env['ir.attachment'].create({
            'name': 'existing.txt', 'raw': b'Existing record',
            'res_model': 'res.partner', 'res_id': self.partner.id,
        })
        invalid_wizard = self.env['qing.crm.followup.wizard'].with_user(self.salesperson).create({
            'attachment_ids': [(6, 0, foreign_attachment.ids)],
            'channel': 'phone', 'note': 'Invalid attachment reuse',
            'result': 'connected', 'role_id': role.id,
        })
        with self.assertRaises(AccessError):
            invalid_wizard.action_confirm()
        wizard = self.env['qing.crm.followup.wizard'].with_user(self.salesperson).create({
            'channel': 'phone',
            'duration_minutes': 12,
            'effective_contact': True,
            'note': 'Confirmed purchasing timeframe.',
            'result': 'interested',
            'role_id': role.id,
        })
        wizard.action_confirm()
        followup = self.env['qing.crm.followup'].with_user(self.salesperson).search([])
        self.assertEqual(len(followup), 1)
        self.assertEqual(followup.owner_id, self.salesperson)
        self.assertEqual(followup.partner_name, self.partner.display_name)
        self.assertEqual(followup.duration_minutes, 12)
        self.assertTrue(role.last_effective_followup_at)
        with self.assertRaises(AccessError):
            followup.write({'note': 'Changed'})
        role.with_user(self.salesperson)._crm_return(False, 'Follow-up completed')
        self.assertEqual(
            self.env['qing.crm.followup'].with_user(self.salesperson).search_count([
                ('id', '=', followup.id)]), 1,
        )

    def test_status_change_requires_reason_and_creates_history(self):
        role = self._enroll_public()
        self.env['qing.crm.pool.entry'].with_user(self.salesperson).search([]).action_claim()
        with self.assertRaises(UserError):
            role.with_user(self.salesperson)._crm_update_status('qualified', 'healthy', 'b', '')
        role.with_user(self.salesperson)._crm_update_status('qualified', 'healthy', 'b', 'Budget confirmed')
        self.assertEqual(role.lifecycle_stage, 'qualified')
        event = self.env['qing.crm.customer.event'].with_user(self.salesperson).search([
            ('partner_role_id', '=', role.id), ('event_type', '=', 'status')])
        self.assertEqual(event.reason, 'Budget confirmed')
        self.assertEqual(event.previous_lifecycle_stage, 'potential')
        with self.assertRaises(AccessError):
            role.with_user(self.manager).write({'lifecycle_stage': 'lost'})

    def test_return_to_pool_removes_private_sharing(self):
        role = self._enroll_public()
        self.env['qing.crm.pool.entry'].with_user(self.salesperson).search([]).action_claim()
        role.sudo().write({'shared_user_ids': [(4, self.other_salesperson.id)]})
        role.with_user(self.salesperson)._crm_return(False, 'No capacity this month')
        self.assertEqual(role.pool_state, 'public')
        self.assertFalse(role.effective_owner_id)
        self.assertFalse(role.shared_user_ids)
        self.assertTrue(self.env['qing.crm.pool.entry'].with_user(self.other_salesperson).search([]))
