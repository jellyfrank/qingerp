from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from ..hooks import uninstall_hook


@tagged('post_install', '-at_install')
class TestResetDraft(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.billing_user = new_test_user(
            cls.env, login='qing_billing', groups='account.group_account_invoice',
            company_id=cls.env.company.id,
            company_ids=[(6, 0, cls.env.company.ids)],
        )
        cls.internal_user = new_test_user(cls.env, login='qing_internal', groups='base.group_user')
        cls.manager_user = new_test_user(
            cls.env, login='qing_account_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id,
            company_ids=[(6, 0, cls.env.company.ids)],
        )
        cls.reset_action = cls.env.ref('account.accountant_reset_to_draft_entries_action')

    def _invoices(self, count=2):
        return self.env['account.move'].create([
            {
                'move_type': 'out_invoice',
                'partner_id': self.partner_a.id,
                'invoice_date': fields.Date.today(),
                'invoice_line_ids': [(0, 0, {
                    'name': 'Qing batch reset test',
                    'account_id': self.company_data['default_account_revenue'].id,
                    'quantity': 1,
                    'price_unit': 100,
                    'tax_ids': [(5, 0, 0)],
                })],
            }
            for _ in range(count)
        ])

    def _run_action(self, moves, user=None):
        return self.reset_action.with_user(user or self.billing_user).with_context(
            active_model='account.move', active_ids=moves.ids,
        ).run()

    def test_billing_binding_and_batch_reset(self):
        self.assertFalse(self.billing_user.has_group('account.group_account_user'))
        bindings = self.env['ir.actions.actions'].with_user(self.billing_user).get_bindings('account.move')
        self.assertEqual(sum(a['id'] == self.reset_action.id for a in bindings['action']), 1)
        invoices = self._invoices()
        invoices.action_post()
        invoices.review_state = 'todo'
        result = self._run_action(invoices)
        self.assertEqual(invoices.mapped('state'), ['draft', 'draft'])
        self.assertEqual(result['tag'], 'soft_reload')

    def test_unauthorized_user(self):
        invoices = self._invoices()
        invoices.action_post()
        with self.assertRaises(AccessError):
            self._run_action(invoices, self.internal_user)
        self.assertEqual(invoices.mapped('state'), ['posted', 'posted'])

    def test_locked_entry_keeps_native_protection(self):
        invoice = self._invoices(1)
        invoice.action_post()
        invoice.button_hash()
        self.assertTrue(invoice.inalterable_hash)
        with self.assertRaises(UserError):
            self._run_action(invoice)
        self.assertEqual(invoice.state, 'posted')

    def test_mixed_draft_and_posted_uses_native_behavior(self):
        invoices = self._invoices()
        invoices[0].action_post()
        invoices.review_state = 'todo'
        result = self._run_action(invoices)
        self.assertEqual(invoices.mapped('state'), ['draft', 'draft'])
        self.assertEqual(result['tag'], 'soft_reload')

    def test_reviewed_invoice_uses_odoo_20_reset_behavior(self):
        invoice = self._invoices(1)
        invoice.action_post()
        invoice.review_state = 'reviewed'
        self._run_action(invoice)
        self.assertEqual(invoice.state, 'draft')

    def test_account_manager_can_open_native_chart_of_accounts(self):
        chart_menu = self.env.ref('account.menu_action_account_form')
        manager_group = self.env.ref('account.group_account_manager')
        self.assertFalse(self.manager_user.has_group('account.group_account_readonly'))
        self.assertIn(manager_group, chart_menu.group_ids)
        visible_menu_ids = self.env['ir.ui.menu'].with_user(self.manager_user)._visible_menu_ids()
        self.assertIn(chart_menu.id, visible_menu_ids)
        self.assertEqual(chart_menu.action.res_model, 'account.account')
        self.assertTrue(self.env['account.account'].with_user(self.manager_user).has_access('read'))

    def test_uninstall_restores_native_group(self):
        chart_menu = self.env.ref('account.menu_action_account_form')
        manager_group = self.env.ref('account.group_account_manager')
        uninstall_hook(self.env)
        self.assertNotIn(self.env.ref('account.group_account_invoice'), self.reset_action.group_ids)
        self.assertIn(self.env.ref('account.group_account_user'), self.reset_action.group_ids)
        self.assertNotIn(manager_group, chart_menu.group_ids)
