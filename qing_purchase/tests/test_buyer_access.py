from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPurchaseBuyerAccess(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.buyer = new_test_user(cls.env, login='purchase_scope_buyer',
                                  groups='purchase.group_purchase_user')
        cls.peer = new_test_user(cls.env, login='purchase_scope_peer',
                                 groups='purchase.group_purchase_user')
        cls.manager = new_test_user(cls.env, login='purchase_scope_manager',
                                    groups='purchase.group_purchase_manager')
        cls.accountant = new_test_user(cls.env, login='purchase_scope_accountant',
            groups='purchase.group_purchase_user,account.group_account_user')
        cls.system = new_test_user(cls.env, login='purchase_scope_system',
            groups='purchase.group_purchase_user,base.group_system')
        cls.invoice_user = new_test_user(cls.env, login='purchase_scope_invoice',
            groups='purchase.group_purchase_user,account.group_account_invoice')
        cls.product = cls._create_product(name='Buyer scope service',
                                          type='service', purchase_method='purchase')

    def _order(self, buyer, count=1):
        return self.env['purchase.order'].create({
            'partner_id': self.partner_a.id, 'user_id': buyer.id if buyer else False,
            'order_line': [Command.create({
                'product_id': self.product.id, 'product_qty': 1, 'price_unit': 100,
                'tax_ids': [Command.clear()],
            }) for _index in range(count)],
        })

    def _bill(self, orders=(), kind='in_invoice', creator=None):
        lines = orders.order_line if orders else self.env['purchase.order.line']
        values = {
            'partner_id': self.partner_a.id, 'move_type': kind,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'product_id': self.product.id, 'quantity': 1, 'price_unit': 100,
                'purchase_line_id': line.id,
            }) for line in lines] or [Command.create({
                'product_id': self.product.id, 'quantity': 1, 'price_unit': 100,
            })],
        }
        return self.env['account.move'].with_user(creator or self.env.user).create(values)

    def _visible(self, records, user=None):
        return records.with_user(user or self.buyer).search([('id', 'in', records.ids)])

    def test_orders_lines_search_read_group_and_rpc(self):
        own = self._order(self.buyer)
        peer = self._order(self.peer)
        unassigned = self._order(False)
        orders = own | peer | unassigned
        self.assertEqual(self._visible(orders), own.with_user(self.buyer))
        self.assertEqual(self._visible(orders.order_line).ids, own.order_line.ids)
        self.assertEqual(orders.with_user(self.buyer).search_count([('id', 'in', orders.ids)]), 1)
        grouped = orders.with_user(self.buyer)._read_group(
            [('id', 'in', orders.ids)], ['user_id'], ['amount_total:sum'])
        self.assertEqual([(user.id, total) for user, total in grouped], [(self.buyer.id, 100)])
        for records in (peer, peer.order_line, unassigned):
            with self.assertRaises(AccessError):
                records.with_user(self.buyer).read(['display_name'])
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                records.with_user(self.buyer).write({'name': 'Forbidden'})

    def test_purchase_administrators_and_full_accountants_keep_all(self):
        orders = self._order(self.buyer) | self._order(self.peer) | self._order(False)
        bills = self._bill(orders[:1]) | self._bill(orders[1:2]) | self._bill(orders[:2])
        for user in (self.manager, self.accountant, self.system):
            self.assertEqual(set(self._visible(orders, user).ids), set(orders.ids))
            self.assertEqual(set(self._visible(bills, user).ids), set(bills.ids))

    def test_duplicate_warning_does_not_reference_hidden_orders(self):
        own = self._order(self.buyer)
        same_buyer = self._order(self.buyer)
        other = self._order(self.peer)
        (own | same_buyer | other).partner_ref = 'QA shared vendor reference'
        self.assertEqual(own.with_user(self.buyer).duplicated_order_ids.ids, same_buyer.ids)
        self.assertEqual(set(own.with_user(self.manager).duplicated_order_ids.ids),
                         set((same_buyer | other).ids))

    def test_bills_refunds_receipts_and_all_journal_items(self):
        own = self._order(self.buyer)
        peer = self._order(self.peer)
        mine = self.env['account.move']
        others = self.env['account.move']
        for kind in ('in_invoice', 'in_refund', 'in_receipt'):
            mine |= self._bill(own, kind=kind)
            others |= self._bill(peer, kind=kind)
        self.assertEqual(set(self._visible(mine | others).ids), set(mine.ids))
        self.assertEqual(set(self._visible((mine | others).line_ids).ids), set(mine.line_ids.ids))
        with self.assertRaises(AccessError):
            others[:1].with_user(self.buyer).read(['amount_total'])
        with self.assertRaises(AccessError):
            others.line_ids.with_user(self.buyer).read(['balance'])

    def test_mixed_buyers_cannot_leak_through_hidden_source_lines(self):
        own = self._order(self.buyer)
        peer = self._order(self.peer)
        mixed = self._bill(own | peer)
        unassigned = self._bill(own | self._order(False))
        for user in (self.buyer, self.peer, self.invoice_user):
            self.assertFalse(self._visible(mixed | unassigned, user))
            self.assertFalse(self._visible((mixed | unassigned).line_ids, user))
        # Standard invoicing access must not union away the buyer restriction.
        other = self._bill(peer)
        self.assertFalse(self._visible(other, self.invoice_user))

    def test_manual_bills_follow_creator_without_using_customer_salesperson(self):
        mine = self._bill(creator=self.buyer)
        other = self._bill(creator=self.peer)
        other.invoice_user_id = self.buyer
        self.assertEqual(self._visible(mine | other).ids, mine.ids)
        own_linked = self._bill(self._order(self.buyer), creator=self.accountant)
        self.assertEqual(self._visible(own_linked).ids, own_linked.ids)
        own_linked.invoice_user_id = self.peer
        self.assertEqual(self._visible(own_linked).ids, own_linked.ids)

    def test_owner_reassignment_and_group_changes_apply_immediately(self):
        order = self._order(self.buyer)
        bill = self._bill(order)
        self.assertEqual(self._visible(bill).ids, bill.ids)
        order.user_id = self.peer
        self.assertFalse(self._visible(order))
        self.assertFalse(self._visible(bill))
        self.buyer.group_ids |= self.env.ref('purchase.group_purchase_manager')
        self.assertEqual(self._visible(order).ids, order.ids)
        self.assertEqual(self._visible(bill).ids, bill.ids)
        self.buyer.group_ids -= self.env.ref('purchase.group_purchase_manager')
        self.assertFalse(self._visible(order))
        self.assertFalse(self._visible(bill))

    def test_assignment_and_foreign_line_rpc_cannot_bypass_scope(self):
        own = self._order(self.buyer)
        other = self._order(self.peer)
        bill = self._bill(own)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            own.with_user(self.buyer).write({'user_id': self.peer.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            own.with_user(self.buyer).copy({'user_id': self.peer.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._order(self.peer).with_user(self.buyer).copy()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            own.order_line.with_user(self.buyer).write({'order_id': other.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            bill.invoice_line_ids.with_user(self.buyer).write({'purchase_line_id': other.order_line.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._bill(other, creator=self.buyer)
        other_bill = self._bill(other)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            bill.invoice_line_ids.with_user(self.buyer).write({'move_id': other_bill.id})

    def test_order_bill_creation_retains_native_permissions(self):
        order = self._order(self.buyer)
        order.button_confirm()
        order.with_user(self.buyer).action_qing_create_bill()
        bill = order.invoice_ids
        self.assertEqual(self._visible(bill).ids, bill.ids)
        self.assertEqual(bill.state, 'draft')
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            bill.with_user(self.buyer).action_post()
        self.assertFalse(self.buyer.has_group('account.group_account_manager'))

    def test_reports_and_matching_autocomplete_share_the_same_scope(self):
        own = self._order(self.buyer)
        other = self._order(self.peer)
        (own | other).button_confirm()
        mine = self._bill(own)
        theirs = self._bill(other)
        mixed = self._bill(own | other)
        reports = self.env['purchase.report'].with_user(self.buyer).search([
            ('order_id', 'in', (own | other).ids)])
        self.assertEqual(reports.order_id.ids, own.ids)
        matches = self.env['purchase.bill.line.match'].with_user(self.buyer).search([
            '|', ('purchase_order_id', 'in', (own | other).ids),
            ('account_move_id', 'in', (mine | theirs | mixed).ids)])
        self.assertEqual(set(matches.purchase_order_id.ids), set(own.ids))
        self.assertEqual(set(matches.account_move_id.ids), set(mine.ids))
        (mine | theirs | mixed).action_post()
        union = self.env['purchase.bill.union'].with_user(self.buyer).search([
            ('vendor_bill_id', 'in', (mine | theirs | mixed).ids)])
        self.assertEqual(union.vendor_bill_id.ids, mine.ids)
        invoice_reports = self.env['account.invoice.report'].with_user(self.invoice_user).search([
            ('move_id', 'in', (mine | theirs | mixed).ids)])
        self.assertFalse(invoice_reports)

    def test_multi_company_boundary_still_applies_to_manager(self):
        other = self.setup_other_company()['company']
        self.manager.company_ids |= other
        order = self._order(self.manager)
        scoped = order.with_user(self.manager).with_context(allowed_company_ids=other.ids)
        self.assertFalse(scoped.search([('id', '=', order.id)]))
        with self.assertRaises(AccessError):
            scoped.read(['name'])

    def test_large_bills_use_bounded_security_queries(self):
        counts = []
        for size in (50, 150):
            order = self._order(self.buyer, count=size)
            bill = self._bill(order, creator=self.buyer)
            self.env.flush_all()
            Bill = self.env['account.move'].with_user(self.buyer)
            Bill.search([('id', '=', bill.id)])
            before = self.env.cr.sql_log_count
            self.assertEqual(Bill.search_count([('id', '=', bill.id)]), 1)
            counts.append(self.env.cr.sql_log_count - before)
        self.assertLessEqual(counts[1], counts[0] + 2)
