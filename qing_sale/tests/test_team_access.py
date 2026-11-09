from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSalesTeamAccess(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('sales_team.group_sale_manager')
        groups = 'sales_team.group_sale_salesman_all_leads,qing_sale.group_sale_team_restricted'
        cls.member = new_test_user(cls.env, login='team_scope_member', groups=groups)
        cls.peer = new_test_user(cls.env, login='team_scope_peer', groups=groups)
        cls.other = new_test_user(cls.env, login='team_scope_other', groups=groups)
        cls.team = cls.env['crm.team'].create({'name': 'Team scope A', 'user_id': cls.member.id,
                                             'member_ids': [Command.set([cls.member.id, cls.peer.id])]})
        cls.other_team = cls.env['crm.team'].create({'name': 'Team scope B', 'member_ids': [Command.set(cls.other.ids)]})

    def _order(self, team, user):
        return self.env['sale.order'].create({'partner_id': self.partner_a.id,
            'team_id': team.id, 'user_id': user.id, 'order_line': [Command.create({
                'product_id': self.product_a.id, 'product_uom_qty': 1, 'price_unit': 100})]})

    def test_peer_visible_other_team_hidden_and_rpc_blocked(self):
        own = self._order(self.team, self.peer)
        other = self._order(self.other_team, self.other)
        orders = self.env['sale.order'].with_user(self.member).search([('id', 'in', (own | other).ids)])
        self.assertEqual(orders.ids, own.ids)
        own.with_user(self.member).check_access('write')
        with self.assertRaises(AccessError):
            other.with_user(self.member).read(['name'])
        with self.assertRaises(AccessError):
            other.order_line.with_user(self.member).read(['price_unit'])
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            own.with_user(self.member).write({'team_id': self.other_team.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            own.order_line.with_user(self.member).write({'order_id': other.id})

    def test_invoices_refunds_lines_and_full_accountant(self):
        moves = self.env['account.move'].create([{
            'partner_id': self.partner_a.id, 'move_type': kind, 'team_id': team.id,
            'invoice_line_ids': [Command.create({'product_id': self.product_a.id, 'quantity': 1, 'price_unit': 100})],
        } for team in (self.team, self.other_team) for kind in ('out_invoice', 'out_refund')])
        own = moves.filtered(lambda move: move.team_id == self.team)
        visible = self.env['account.move'].with_user(self.member).search([('id', 'in', moves.ids)])
        self.assertEqual(set(visible.ids), set(own.ids))
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            own[:1].with_user(self.member).write({'team_id': self.other_team.id})
        with self.assertRaises(AccessError):
            (moves - own).invoice_line_ids.with_user(self.member).read(['price_unit'])
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            own[:1].invoice_line_ids.with_user(self.member).write({'move_id': (moves - own)[0].id})
        accountant = new_test_user(self.env, login='team_scope_accountant',
            groups='account.group_account_manager,qing_sale.group_sale_team_restricted')
        self.assertEqual(len(self.env['account.move'].with_user(accountant).search([('id', 'in', moves.ids)])), 4)

    def test_unassigned_hidden_and_membership_changes_live(self):
        order = self._order(self.other_team, self.other)
        Orders = self.env['sale.order'].with_user(self.member)
        self.assertFalse(Orders.search_count([('id', '=', order.id)]))
        self.other_team.member_ids |= self.member
        self.assertEqual(Orders.search_count([('id', '=', order.id)]), 1)
        self.other_team.member_ids -= self.member
        self.assertFalse(Orders.search_count([('id', '=', order.id)]))
        order.team_id = False
        self.assertFalse(Orders.search_count([('id', '=', order.id)]))
