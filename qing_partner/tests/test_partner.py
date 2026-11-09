from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged, new_test_user, Form
from odoo.addons.qing_base.tools import strict_computes


@tagged('post_install', '-at_install')
class TestPartnerRoles(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env['res.company'].create({'name': 'Partner Company B'})
        cls.seller = new_test_user(cls.env, login='partner_seller', groups='sales_team.group_sale_salesman,qing_partner.group_partner_editor,base.group_allow_export', company_id=cls.company.id, company_ids=[Command.set([cls.company.id, cls.other_company.id])])
        cls.buyer = new_test_user(cls.env, login='partner_buyer', groups='purchase.group_purchase_user,qing_partner.group_partner_editor', company_id=cls.company.id)
        cls.reader = new_test_user(cls.env, login='partner_reader', groups='base.group_user', company_id=cls.company.id)
        cls.manager = new_test_user(cls.env, login='partner_manager', groups='qing_partner.group_partner_manager', company_id=cls.company.id)
        cls.seller.write({'qing_partner_access_mode': 'restricted', 'qing_customer_scope': 'own', 'qing_supplier_scope': 'none'})
        cls.buyer.write({'qing_partner_access_mode': 'restricted', 'qing_customer_scope': 'none', 'qing_supplier_scope': 'own'})
        cls.customer, cls.supplier, cls.dual, cls.hidden = cls.env['res.partner'].create([
            {'name': 'Customer', 'is_company': True}, {'name': 'Supplier', 'is_company': True},
            {'name': 'Dual', 'is_company': True}, {'name': 'Hidden', 'is_company': True},
        ])
        cls.child = cls.env['res.partner'].create({'name': 'Customer Delivery', 'parent_id': cls.customer.id, 'type': 'delivery'})
        cls.Role = cls.env['qing.partner.role']
        cls.customer_role, cls.supplier_role, cls.dual_customer, cls.dual_supplier = cls.Role.create([
            {'partner_id': cls.customer.id, 'company_id': cls.company.id, 'role': 'customer', 'state': 'enabled', 'override_owner_id': cls.seller.id},
            {'partner_id': cls.supplier.id, 'company_id': cls.company.id, 'role': 'supplier', 'state': 'enabled', 'override_owner_id': cls.buyer.id},
            {'partner_id': cls.dual.id, 'company_id': cls.company.id, 'role': 'customer', 'state': 'enabled', 'override_owner_id': cls.seller.id},
            {'partner_id': cls.dual.id, 'company_id': cls.company.id, 'role': 'supplier', 'state': 'enabled', 'override_owner_id': cls.buyer.id},
        ])

    def _visible(self, user, partners=None, **context):
        partners = partners if partners is not None else self.customer | self.supplier | self.dual | self.hidden | self.child
        return self.env['res.partner'].with_user(user).with_context(allowed_company_ids=[self.company.id], **context).search([('id', 'in', partners.ids)])

    def test_rank_is_not_authorization(self):
        self.hidden.write({'customer_rank': 50, 'supplier_rank': 60})
        self.assertEqual(set(self._visible(self.seller).ids), set((self.customer | self.dual | self.child).ids))
        self.assertEqual(set(self._visible(self.buyer).ids), set((self.supplier | self.dual).ids))
        self.assertEqual(self.customer.customer_rank, 0)

    def test_direct_read_and_export_blocked(self):
        record = self.hidden.with_user(self.seller)
        with self.assertRaises(AccessError):
            record.read(['name'])
        with self.assertRaises(AccessError):
            record.export_data(['name'])
        self.assertFalse(record.has_access('read'))

    def test_shared_default_and_restricted_none(self):
        self.assertEqual(len(self._visible(self.reader)), 5)
        self.reader.qing_partner_access_mode = 'restricted'
        self.assertFalse(self._visible(self.reader))
        self.reader.partner_id.with_user(self.reader).read(['name'])
        self.company.partner_id.with_user(self.reader).read(['name'])

    def test_company_and_owner_match_same_role(self):
        self.customer_role.override_owner_id = self.buyer
        self.Role.create({'partner_id': self.customer.id, 'company_id': self.other_company.id, 'role': 'customer', 'state': 'enabled', 'override_owner_id': self.seller.id})
        self.assertFalse(self._visible(self.seller, self.customer))
        found = self.env['res.partner'].with_user(self.seller).with_context(allowed_company_ids=[self.other_company.id]).search([('id', '=', self.customer.id)])
        self.assertEqual(found.id, self.customer.id)
        with self.assertRaises(AccessError):
            self.env['res.partner'].with_user(self.buyer).with_context(allowed_company_ids=[self.other_company.id]).search([])

    def test_roles_union_and_revocation(self):
        self.seller.qing_supplier_scope = 'own'
        self.supplier_role.shared_user_ids = [Command.link(self.seller.id)]
        self.assertIn(self.supplier.id, self._visible(self.seller).ids)
        self.supplier_role.shared_user_ids = [Command.clear()]
        self.assertNotIn(self.supplier.id, self._visible(self.seller).ids)
        self.assertIn(self.customer.id, self._visible(self.seller).ids)

    def test_team_membership_is_live_and_protected(self):
        team = self.env['crm.team'].create({'name': 'Sales', 'company_id': self.company.id, 'member_ids': [Command.set([self.seller.id, self.buyer.id])]})
        self.customer_role.override_owner_id = self.buyer
        self.assertFalse(self._visible(self.seller, self.customer))
        self.seller.qing_customer_scope = 'team'
        self.assertTrue(self._visible(self.seller, self.customer))
        team.member_ids = [Command.clear()]
        self.assertFalse(self._visible(self.seller, self.customer))

    def test_cannot_self_grant_or_change_role(self):
        with self.assertRaises(AccessError):
            self.seller.with_user(self.seller).write({'qing_customer_scope': 'all'})
        with self.assertRaises(AccessError):
            self.customer_role.with_user(self.seller).write({'shared_user_ids': [Command.link(self.reader.id)]})
        with self.assertRaises(AccessError):
            self.Role.with_user(self.seller).create({'partner_id': self.hidden.id, 'company_id': self.company.id, 'role': 'customer', 'override_owner_id': self.seller.id})

    def test_master_data_write_and_child_hierarchy(self):
        self.customer.with_user(self.seller).write({'phone': '123456'})
        for values in [{'name': 'Changed'}, {'vat': 'CN123'}, {'parent_id': self.hidden.id}, {'company_id': self.other_company.id}]:
            with self.assertRaises(AccessError):
                self.customer.with_user(self.seller).write(values)
        with self.assertRaises(AccessError):
            self.customer.with_user(self.reader).write({'phone': '123'})
        self.customer.with_user(self.manager).write({'name': 'Managed name'})

    def test_unclassified_creation_and_contact_inheritance(self):
        partner = self.env['res.partner'].with_user(self.seller).create({'name': 'New prospect'})
        self.assertEqual(self._visible(self.seller, partner).id, partner.id)
        child = self.env['res.partner'].with_user(self.seller).create({'name': 'New Contact', 'parent_id': self.customer.id})
        self.assertEqual(self._visible(self.seller, child).id, child.id)
        with self.assertRaises(AccessError):
            self.env['res.partner'].with_user(self.seller).create({'name': 'Hidden child', 'parent_id': self.hidden.id})

    def test_disabled_role_keeps_history_but_blocks_new_business(self):
        order = self.env['sale.order'].create({'partner_id': self.customer.id, 'user_id': self.seller.id})
        self.company.qing_partner_enforce_roles = True
        self.customer_role.state = 'disabled'
        self.assertIn(self.customer.id, self._visible(self.seller).ids)
        order.with_user(self.seller).read(['partner_id'])
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['sale.order'].create({'partner_id': self.customer.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order.action_confirm()
        self.assertEqual(order.state, 'draft')

    def test_sale_purchase_admission_and_context_cannot_bypass(self):
        self.company.qing_partner_enforce_roles = True
        self.env['sale.order'].create({'partner_id': self.customer.id})
        self.env['purchase.order'].create({'partner_id': self.supplier.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['sale.order'].with_context(qing_skip_security=True, qing_role_company_id=self.other_company.id).create({'partner_id': self.hidden.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['purchase.order'].create({'partner_id': self.customer.id})

    def test_partner_change_and_direct_state_write_checked(self):
        order = self.env['sale.order'].create({'partner_id': self.customer.id})
        purchase = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.company.qing_partner_enforce_roles = True
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order.write({'partner_id': self.hidden.id})
        self.supplier_role.state = 'disabled'
        with self.assertRaises(ValidationError), self.cr.savepoint():
            purchase.write({'state': 'purchase'})

    def test_role_search_is_current_company_and_inherits(self):
        Partner = self.env['res.partner'].with_user(self.seller).with_context(allowed_company_ids=[self.company.id])
        self.assertIn(self.child.id, Partner.search([('qing_is_customer', '=', True)]).ids)
        self.assertFalse(Partner.search([('id', '=', self.customer.id), ('qing_is_customer', '=', False)]))
        self.assertFalse(Partner.with_context(qing_role_company_id=self.other_company.id, allowed_company_ids=[self.company.id, self.other_company.id]).search([('id', '=', self.customer.id), ('qing_is_customer', '=', True)]))

    def test_company_assignment_and_commercial_entity_validation(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Role.create({'partner_id': self.child.id, 'company_id': self.company.id, 'role': 'customer'})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Role.create({'partner_id': self.hidden.id, 'company_id': self.other_company.id, 'role': 'customer', 'override_owner_id': self.buyer.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.customer.write({'parent_id': self.hidden.id, 'is_company': False})
        with self.assertRaises(ValidationError):
            self.customer_role.write({'role': 'supplier'})

    def test_seed_pending_idempotent_without_automatic_access(self):
        self.hidden.customer_rank = 5
        wizard = self.env['qing.partner.role.seed'].create({'company_id': self.company.id, 'partner_ids': [Command.set([self.hidden.id])], 'use_rank_hints': True})
        wizard.action_seed()
        wizard.action_seed()
        roles = self.Role.search([('partner_id', '=', self.hidden.id)])
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles.state, 'pending')
        self.assertFalse(roles.effective_owner_id | roles.shared_user_ids)
        self.assertFalse(self._visible(self.seller, self.hidden))
        self.assertEqual(self.hidden.customer_rank, 5)

    def test_no_role_delete_and_no_inherited_roles_on_copy(self):
        with self.assertRaises(AccessError):
            self.customer_role.with_user(self.manager).unlink()
        duplicate = self.customer.copy()
        self.assertFalse(duplicate.qing_role_ids)

    def test_portal_keeps_native_boundary(self):
        portal = new_test_user(self.env, login='partner_portal', groups='base.group_portal')
        portal.partner_id.with_user(portal).read(['name'])
        with self.assertRaises(AccessError):
            self.customer.with_user(portal).read(['name'])
        with self.assertRaises(AccessError):
            self.Role.with_user(portal).search([])

    def test_role_read_does_not_expose_other_owner(self):
        roles = self.Role.with_user(self.seller).search([])
        self.assertEqual(set(roles.ids), {self.customer_role.id, self.dual_customer.id})
        with self.assertRaises(AccessError):
            self.supplier_role.with_user(self.seller).read(['effective_owner_id'])

    def test_real_forms_and_views(self):
        # Python Form consumes model fields, not the browser read metadata.
        with strict_computes(), Form(self.customer) as form:
            self.assertTrue(form.qing_is_customer)
        for model, xmlid in [('res.users', 'base.view_users_form'), ('sale.order', 'sale.view_order_form'), ('purchase.order', 'purchase.purchase_order_form')]:
            self.env[model].with_user({'sale.order': self.seller, 'purchase.order': self.buyer}.get(model, self.env.user)).get_view(self.env.ref(xmlid).id, 'form')

    def test_visible_records_can_be_read_in_batch(self):
        visible = self._visible(self.seller)
        self.assertEqual(len(visible.read(['name', 'qing_is_customer'])), 3)

    def test_bank_accounts_follow_contact_scope(self):
        account = self.env['res.partner.bank'].create({'partner_id': self.hidden.id, 'account_number': '123456789'})
        with self.assertRaises(AccessError):
            account.with_user(self.seller).read(['account_number'])
        self.assertFalse(self.env['res.partner.bank'].with_user(self.seller).search([('id', '=', account.id)]))

    def test_allowed_company_context_does_not_reuse_cached_access(self):
        role = self.Role.create({'partner_id': self.hidden.id, 'company_id': self.other_company.id, 'role': 'customer', 'override_owner_id': self.seller.id})
        partner = self.hidden.with_user(self.seller)
        role.with_user(self.seller).with_context(allowed_company_ids=[self.company.id, self.other_company.id]).read(['state'])
        partner.with_context(allowed_company_ids=[self.company.id, self.other_company.id]).read(['name'])
        with self.assertRaises(AccessError):
            partner.with_context(allowed_company_ids=[self.company.id]).read(['name'])

    def test_context_default_cannot_change_ownership_or_identity(self):
        with self.assertRaises(AccessError):
            self.env['res.partner'].with_user(self.seller).with_context(default_user_ids=[Command.link(self.seller.id)]).create({'name': 'Escalation'})

    def test_bank_details_require_finance_or_master_manager(self):
        bank = self.env['res.partner.bank'].create({'partner_id': self.customer.id, 'account_number': '987654321'})
        with self.assertRaises(AccessError):
            bank.with_user(self.seller).read(['account_number'])
        self.assertEqual(bank.with_user(self.manager).read(['account_number'])[0]['account_number'], '987654321')

    def test_invoice_checks_and_refund_escape_for_correction(self):
        self.company.qing_partner_enforce_roles = True
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': self.hidden.id})
        invoice = self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': self.customer.id})
        self.customer_role.state = 'disabled'
        with self.assertRaises(ValidationError), self.cr.savepoint():
            invoice._post()
        refund = self.env['account.move'].create({'move_type': 'out_refund', 'partner_id': self.customer.id})
        self.assertEqual(refund.move_type, 'out_refund')

    def test_import_respects_roles(self):
        self.company.qing_partner_enforce_roles = True
        result = self.env['sale.order'].load(['partner_id/.id'], [[str(self.hidden.id)]])
        self.assertTrue(result['messages'])
        self.assertFalse(result['ids'])

    def test_role_seed_uses_company_documents_without_ranks(self):
        self.env['sale.order'].create({'partner_id': self.hidden.id})
        wizard = self.env['qing.partner.role.seed'].create({'company_id': self.company.id, 'partner_ids': [Command.set([self.hidden.id])]})
        wizard.action_seed()
        role = self.Role.search([('partner_id', '=', self.hidden.id)])
        self.assertEqual(role.role, 'customer')
        self.assertEqual(role.state, 'pending')

    def test_new_assignment_revokes_cached_denial(self):
        partner = self.hidden.with_user(self.seller)
        self.assertFalse(partner.has_access('read'))
        self.Role.create({'partner_id': self.hidden.id, 'company_id': self.company.id, 'role': 'customer', 'override_owner_id': self.seller.id})
        self.assertTrue(partner.has_access('read'))
        partner.read(['name'])

    def test_native_actions_and_safe_uninstall_context(self):
        import ast
        from odoo.addons.qing_partner.hooks import uninstall_hook
        action = self.env.ref('account.res_partner_action_customer')
        self.assertTrue(ast.literal_eval(action.context)['search_default_qing_customer'])
        uninstall_hook(self.env)
        self.assertTrue(ast.literal_eval(action.context)['search_default_customer'])
        action.context = "{'custom_marker': True}"
        uninstall_hook(self.env)
        self.assertTrue(ast.literal_eval(action.context)['custom_marker'])

    def test_confirm_and_line_edit_50_150(self):
        import logging
        import time
        product = self.env['product.product'].create({'name': 'Partner scope performance product', 'type': 'consu', 'list_price': 10})
        self.company.qing_partner_enforce_roles = True
        for count in (50, 150):
            order = self.env['sale.order'].create({
                'partner_id': self.customer.id,
                'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 1}) for _ in range(count)],
            })
            order.action_confirm()
            self.assertEqual(order.state, 'sale')
            self.env.flush_all()
            start, queries = time.monotonic(), self.env.cr.sql_log_count
            order.order_line[0].product_uom_qty = 2
            self.env.flush_all()
            logging.getLogger(__name__).info('PARTNER_PERF lines=%s edit_seconds=%.3f edit_queries=%s', count, time.monotonic() - start, self.env.cr.sql_log_count - queries)
            self.assertEqual(order.order_line[0].product_uom_qty, 2)

    def test_own_company_bank_remains_available_for_reports(self):
        bank = self.env['res.partner.bank'].create({'partner_id': self.company.partner_id.id, 'company_id': self.company.id, 'account_number': 'COMPANY123'})
        self.assertTrue(bank.with_user(self.seller).read(['account_number']))

    def test_editor_can_save_normal_contact_form(self):
        with strict_computes(), Form(self.customer.with_user(self.seller)) as form:
            form.phone = '123123'
        self.assertEqual(self.customer.phone, '123123')

    def test_pending_migration_does_not_grant_all_scope(self):
        self.seller.qing_customer_scope = 'all'
        role = self.Role.create({'partner_id': self.hidden.id, 'company_id': self.company.id, 'role': 'customer'})
        self.assertFalse(self._visible(self.seller, self.hidden))
        role.state = 'enabled'
        self.assertTrue(self._visible(self.seller, self.hidden))

    def test_single_entry_identity_and_native_owners(self):
        partner = self.env['res.partner'].create({
            'name': 'Single entry', 'user_id': self.seller.id, 'buyer_id': self.buyer.id,
            'qing_is_customer': True, 'qing_is_supplier': True,
        })
        roles = self.Role.search([('partner_id', '=', partner.id)])
        self.assertEqual(len(roles), 2)
        self.assertTrue(partner.qing_is_customer and partner.qing_is_supplier)
        self.assertEqual(roles.filtered(lambda r: r.role == 'customer').effective_owner_id, self.seller)
        self.assertEqual(roles.filtered(lambda r: r.role == 'supplier').effective_owner_id, self.buyer)
        self.assertTrue(self._visible(self.seller, partner))
        partner.user_id = self.buyer
        self.assertFalse(self._visible(self.seller, partner))
        partner.qing_is_customer = False
        self.assertFalse(partner.qing_is_customer)
        self.assertTrue(partner.qing_is_supplier)
        self.assertNotIn(partner, self.env['res.partner'].search([('qing_is_customer', '=', True)]))
        partner.qing_is_customer = True
        self.assertEqual(self.Role.search_count([('partner_id', '=', partner.id)]), 2)

    def test_native_assignment_and_flags_cannot_self_grant(self):
        for values in ({'user_id': self.seller.id}, {'buyer_id': self.seller.id}, {'qing_is_supplier': True}):
            with self.assertRaises(AccessError):
                self.customer.with_user(self.seller).write(values)
        with self.assertRaises(AccessError):
            self.env['res.partner'].with_user(self.seller).with_context(default_user_id=self.seller.id).create({'name': 'Escalation'})

    def test_company_override_is_optional_and_independent(self):
        self.customer.user_id = self.seller
        self.customer_role.override_owner_id = False
        self.assertEqual(self.customer_role.effective_owner_id, self.seller)
        self.customer_role.override_owner_id = self.buyer
        self.assertEqual(self.customer.user_id, self.seller)
        self.assertEqual(self.customer_role.effective_owner_id, self.buyer)
        self.customer_role.override_owner_id = False
        self.assertEqual(self.customer_role.effective_owner_id, self.seller)

    def test_customer_form_has_single_maintenance_entry(self):
        form = Form(self.env['res.partner'].with_user(self.manager))
        form.name = 'Form identity'
        self.assertTrue(form._get_modifier('user_id', 'invisible'))
        self.assertTrue(form._get_modifier('buyer_id', 'invisible'))
        form.qing_is_customer = True
        self.assertFalse(form._get_modifier('user_id', 'invisible'))
        self.assertTrue(form._get_modifier('buyer_id', 'invisible'))
        form.qing_is_supplier = True
        self.assertFalse(form._get_modifier('buyer_id', 'invisible'))
        form.qing_is_customer = False
        self.assertTrue(form._get_modifier('user_id', 'invisible'))
        self.assertFalse(form._get_modifier('buyer_id', 'invisible'))
        form.qing_is_customer = True
        form.user_id = self.seller
        form.buyer_id = self.buyer
        partner = form.save()
        self.assertTrue(partner.qing_is_customer and partner.qing_is_supplier)
        from lxml import etree
        view = self.env['res.partner'].with_user(self.seller).get_view(view_type='form')
        arch = etree.fromstring(view['arch'])
        self.assertFalse(arch.xpath('//page[@name="qing_roles"]'))
        self.assertEqual(len(arch.xpath('//page[@name="sales_purchases"]//field[@name="qing_is_customer"]')), 1)
