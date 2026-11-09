import logging

from psycopg2 import IntegrityError

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools.binary import BinaryBytes


_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestFapiao(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='fapiao_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='fapiao_billing', groups='account.group_account_invoice',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])

    def _ticket(self, **values):
        return self.env['qing.fapiao'].with_user(self.manager).create({
            'number': 'TEST0001', 'direction': 'in', 'partner_id': self.partner_a.id,
            'buyer_name': 'Synthetic buyer', 'seller_name': 'Synthetic seller',
            'amount_untaxed': 100, 'amount_tax': 13,
            'source_filename': 'synthetic.txt', 'source_file': BinaryBytes(b'Synthetic tax document. Not a real invoice.'),
            **values})

    def _bill(self, move_type='in_invoice', count=1):
        bill = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.partner_a.id, 'invoice_date': '2026-09-01',
            'invoice_line_ids': [Command.create({'name': 'Synthetic goods', 'quantity': 1, 'price_unit': 113 / count,
                'account_id': self.company_data['default_account_expense'].id,
                'tax_ids': [Command.clear()]}) for i in range(count)]})
        bill.action_post()
        return bill

    def test_register_is_not_posting_and_file_is_frozen(self):
        bill = self._bill()
        ticket = self._ticket(move_ids=[Command.set(bill.ids)])
        before = self.env['account.move'].search_count([])
        ticket.action_register()
        self.assertEqual(ticket.state, 'registered')
        self.assertEqual(ticket.registered_by, self.manager)
        self.assertEqual(bill.with_user(self.manager).action_open_fapiao()['domain'], ['|', ('move_ids', 'in', bill.ids), ('allocation_ids.move_id', 'in', bill.ids)])
        with self.assertRaises(AccessError):
            bill.with_user(self.billing).action_open_fapiao()
        self.assertEqual(ticket.amount_total, 113)
        self.assertEqual(self.env['account.move'].search_count([]), before)
        for vals in ({'amount_tax': 9}, {'source_file': BinaryBytes(b'changed')}, {'move_ids': [Command.clear()]}, {'partner_id': self.partner_b.id}):
            with self.assertRaises(UserError):
                ticket.write(vals)
        with self.assertRaises(UserError):
            ticket.unlink()
        with self.assertRaises(UserError):
            ticket.copy()

    def test_source_link_counts_once_without_claiming_tax_coverage(self):
        bill = self._bill(move_type='out_invoice')
        move = bill.with_user(self.manager)
        ticket = self._ticket(direction='out', move_ids=[Command.set(bill.ids)])
        self.assertEqual(move.qing_fapiao_count, 1)
        self.assertEqual(move.qing_fapiao_status, 'pending_registration')
        ticket.action_register()
        self.assertEqual(move.qing_fapiao_count, 1)
        self.assertEqual(move.qing_fapiao_status, 'unallocated')

    def test_required_data_and_invalid_amount(self):
        ticket = self._ticket(source_file=False)
        with self.assertRaises(UserError):
            ticket.action_register()
        ticket.write({'source_file': BinaryBytes(b'test'), 'amount_untaxed': 0, 'amount_tax': 0})
        with self.assertRaises(UserError):
            ticket.action_register()
        for value in (-1, float('inf')):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                ticket.amount_tax = value

    def test_identity_and_cancel_preserves_original(self):
        ticket = self._ticket(number=' 123 ', code=' 456 ')
        with self.assertRaises((IntegrityError, ValidationError)), self.cr.savepoint():
            self._ticket(number='123', code='456')
        ticket.action_register()
        with self.assertRaises(UserError):
            ticket.action_cancel_registration()
        ticket.cancel_reason = 'Synthetic duplicate recording correction'
        ticket.action_cancel_registration()
        self.assertEqual(ticket.state, 'cancelled')
        self.assertTrue(ticket.source_file)
        self.assertEqual(ticket.cancelled_by, self.manager)
        replacement = self._ticket(number='123', code='456')
        self.assertNotEqual(ticket, replacement)
        with self.assertRaises(UserError):
            ticket.amount_untaxed = 200

    def test_roles_and_company_file_access(self):
        ticket = self._ticket()
        with self.assertRaises(AccessError):
            ticket.with_user(self.billing).read(['source_file'])
        with self.assertRaises(AccessError):
            ticket.with_user(self.billing).action_register()
        other = self.env['res.company'].create({'name': 'Tax other synthetic'})
        self.manager.company_ids = [Command.link(other.id)]
        with self.assertRaises(AccessError):
            ticket.with_context(allowed_company_ids=other.ids).read(['source_file'])
        with self.assertRaises(AccessError):
            self._ticket(state='registered')
        with self.assertRaises(AccessError):
            ticket.write({'state': 'registered'})
        with self.assertRaises(AccessError):
            ticket.write({'amount_total': 999})
        contextual = self.env['qing.fapiao'].with_user(self.manager).with_context(
            default_state='registered', default_registered_by=self.manager.id).create({
                'number': 'CTX-001', 'partner_id': self.partner_a.id,
                'buyer_name': 'Buyer', 'seller_name': 'Seller'})
        self.assertEqual(contextual.state, 'draft')
        self.assertFalse(contextual.registered_by)

    def test_link_validation(self):
        bill = self._bill()
        ticket = self._ticket(direction='out', move_ids=[Command.set(bill.ids)])
        with self.assertRaises(UserError):
            ticket.action_register()
        ticket.direction = 'in'
        ticket.partner_id = self.partner_b
        with self.assertRaises(UserError):
            ticket.action_register()
        ticket.partner_id = self.partner_a
        bill.button_draft()
        with self.assertRaises(UserError):
            ticket.action_register()

    def test_red_and_output_registration(self):
        ticket = self._ticket(direction='out', color='red', move_ids=[Command.set(self._bill('out_refund').ids)])
        with self.assertRaises(UserError):
            ticket.action_register()
        blue = self._ticket(number='ORIGINAL-BLUE', direction='out')
        blue.action_register()
        ticket.write({'original_number': blue.number, 'original_id': blue.id})
        ticket.action_register()
        self.assertEqual(ticket.state, 'registered')

    def test_linked_move_protection_and_payment(self):
        bill = self._bill()
        ticket = self._ticket(move_ids=[Command.set(bill.ids)])
        ticket.action_register()
        for operation in [bill.button_draft, lambda: bill.write({'partner_id': self.partner_b.id}),
                          lambda: bill.invoice_line_ids.write({'price_unit': 120}),
                          lambda: bill.invoice_line_ids.unlink()]:
            with self.assertRaises(UserError):
                operation()
        register = self.env['account.payment.register'].with_context(active_model='account.move', active_ids=bill.ids).create({
            'amount': bill.amount_residual, 'payment_date': '2026-09-02',
            'journal_id': self.company_data['default_journal_bank'].id})
        register._create_payments()
        self.assertEqual(bill.amount_residual, 0)
        self.assertEqual(ticket.state, 'registered')
        ticket.cancel_reason = 'Synthetic wrong link'
        ticket.action_cancel_registration()
        bill.button_draft()
        self.assertEqual(ticket.state, 'cancelled')

    def test_form_defaults_and_amounts(self):
        with Form(self.env['qing.fapiao'].with_user(self.manager)) as form:
            form.partner_id = self.partner_a
            form.number = 'FORM-001'
            form.amount_untaxed = 100
            form.amount_tax = 13
        self.assertEqual(form.record.seller_name, self.partner_a.name)
        self.assertEqual(form.record.buyer_name, self.env.company.name)

    def test_party_partner_links_and_server_side_snapshot_defaults(self):
        ticket = self.env['qing.fapiao'].with_user(self.manager).create({
            'number': 'PARTIES-IN', 'direction': 'in', 'partner_id': self.partner_a.id})
        self.assertEqual(ticket.buyer_partner_id, self.env.company.partner_id.commercial_partner_id)
        self.assertEqual(ticket.seller_partner_id, self.partner_a.commercial_partner_id)
        self.assertEqual(ticket.buyer_name, self.env.company.partner_id.name)
        self.assertEqual(ticket.seller_name, self.partner_a.name)
        output = self.env['qing.fapiao'].with_user(self.manager).create({
            'number': 'PARTIES-OUT', 'direction': 'out', 'partner_id': self.partner_a.id})
        self.assertEqual(output.buyer_partner_id, self.partner_a.commercial_partner_id)
        self.assertEqual(output.seller_partner_id, self.env.company.partner_id.commercial_partner_id)

    def test_large_bill_batch_link(self):
        for count in (50, 150):
            bill = self._bill(count=count)
            ticket = self._ticket(number='LARGE-%s' % count, move_ids=[Command.set(bill.ids)])
            start = self.cr.sql_log_count
            ticket.action_register()
            queries = self.cr.sql_log_count - start
            _logger.info('CNF_FAPIAO_PERF lines=%s registration_queries=%s', count, queries)
            self.assertLess(queries, 100)
            self.assertEqual(len(bill.invoice_line_ids), count)

    def test_output_blue_and_foreign_link_rejected(self):
        bill = self._bill('out_invoice')
        ticket = self._ticket(direction='out', move_ids=[Command.set(bill.ids)])
        other_currency = self.env.ref('base.JPY')
        ticket.currency_id = other_currency
        with self.assertRaises(UserError):
            ticket.action_register()
        ticket.currency_id = bill.currency_id
        ticket.action_register()
        self.assertEqual(ticket.state, 'registered')
        with self.assertRaises(UserError):
            bill.with_user(self.billing).button_draft()
        with self.assertRaises(UserError):
            self.env['account.move.line'].create({'move_id': bill.id,
                'account_id': self.company_data['default_account_expense'].id, 'name': 'Injected'})

    def _allocation(self, ticket, bill, net=50, tax=0):
        return self.env['qing.fapiao.allocation'].with_user(self.manager).create({
            'fapiao_id': ticket.id, 'move_id': bill.id, 'amount_untaxed': net, 'amount_tax': tax})

    def test_allocation_split_capacity_and_cancel(self):
        bill = self._bill()
        a = self._ticket(number='A', amount_untaxed=80, amount_tax=0)
        b = self._ticket(number='B', amount_untaxed=80, amount_tax=0)
        (a | b).action_register()
        first = self._allocation(a, bill, 70)
        first.action_confirm()
        self.assertEqual(a.remaining_untaxed, 10)
        self.assertEqual(bill.qing_fapiao_remaining_untaxed, 43)
        second = self._allocation(b, bill, 44)
        with self.assertRaises(UserError):
            second.action_confirm()
        self.assertEqual(second.state, 'draft')
        second.amount_untaxed = 43
        second.action_confirm()
        self.assertEqual(bill.qing_fapiao_remaining_untaxed, 0)
        with self.assertRaises(UserError):
            bill.with_user(self.billing).button_draft()
        with self.assertRaises(UserError):
            first.write({'amount_untaxed': 20})
        with self.assertRaises(UserError):
            first.action_cancel()
        first.cancel_reason = 'Wrong allocation'
        first.action_cancel()
        self.assertEqual(a.remaining_untaxed, 80)
        self.assertEqual(bill.qing_fapiao_remaining_untaxed, 70)
        self.assertEqual(first.cancelled_by, self.manager)
        b.cancel_reason = 'Wrong ticket'
        b.action_cancel_registration()
        self.assertEqual(bill.qing_fapiao_remaining_untaxed, 113)
        bill.button_draft()

    def test_one_ticket_multiple_bills_and_tax_limit(self):
        bill, other = self._bill(), self._bill()
        ticket = self._ticket(amount_untaxed=100, amount_tax=13)
        ticket.action_register()
        a = self._allocation(ticket, bill, 60)
        b = self._allocation(ticket, other, 40)
        (a | b).action_confirm()
        self.assertEqual(ticket.remaining_untaxed, 0)
        extra = self._allocation(ticket, other, 1)
        with self.assertRaises(UserError):
            extra.action_confirm()
        extra.write({'amount_untaxed': 0, 'amount_tax': 1})
        with self.assertRaises(UserError):
            extra.action_confirm()  # Bill has zero tax; net headroom cannot cover tax.
        self.assertEqual(ticket.remaining_tax, 13)

    def test_allocation_permissions_and_precision(self):
        bill = self._bill()
        ticket = self._ticket()
        line = self._allocation(ticket, bill)
        with self.assertRaises(UserError):
            line.action_confirm()
        with self.assertRaises(AccessError):
            line.with_user(self.billing).read(['amount_untaxed'])
        with self.assertRaises(AccessError):
            line.write({'state': 'confirmed'})
        with self.assertRaises(AccessError):
            line.write({'fapiao_id': ticket.id})
        for amount in [-1, float('nan'), float('inf'), .001]:
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self._allocation(ticket, bill, amount)
        ticket.action_register()
        line.action_confirm()
        with self.assertRaises(UserError):
            line.unlink()
        ticket.cancel_reason = 'Incorrect registration'
        ticket.action_cancel_registration()
        with self.assertRaises(UserError):
            self._allocation(ticket, bill)

    def test_partial_red_separate_amount_tax_and_cancellation(self):
        blue = self._ticket(number='BLUE')
        blue.action_register()
        red = self._ticket(number='RED1', color='red', original_id=blue.id, original_number=blue.number,
                           amount_untaxed=40, amount_tax=5)
        red.action_register()
        self.assertEqual(blue.red_remaining_untaxed, 60)
        self.assertEqual(blue.red_remaining_tax, 8)
        red2 = self._ticket(number='RED2', color='red', original_id=blue.id, original_number=blue.number,
                            amount_untaxed=60, amount_tax=9)
        with self.assertRaises(UserError):
            red2.action_register()
        self.assertEqual(red2.state, 'draft')
        red2.amount_tax = 8
        red2.action_register()
        self.assertEqual(blue.red_remaining_tax, 0)
        blue.cancel_reason = 'Cannot cancel while red exists'
        with self.assertRaises(UserError):
            blue.action_cancel_registration()
        red.cancel_reason = 'Correct red registration'
        red.action_cancel_registration()
        self.assertEqual(blue.red_remaining_untaxed, 40)
        self.assertEqual(blue.red_remaining_tax, 5)

    def test_red_mismatch_and_batch_overflow(self):
        blue = self._ticket(number='BLUE')
        blue.action_register()
        vals = dict(color='red', original_id=blue.id, original_number=blue.number, amount_untaxed=60, amount_tax=6)
        a, b = self._ticket(number='RED-A', **vals), self._ticket(number='RED-B', **vals)
        with self.assertRaises(UserError):
            (a | b).action_register()
        self.assertEqual((a.state, b.state), ('draft', 'draft'))
        a.partner_id = self.partner_b
        with self.assertRaises(UserError):
            a.action_register()
        a.partner_id = self.partner_a
        a.original_number = 'WRONG'
        with self.assertRaises(UserError):
            a.action_register()

    def test_allocation_taxed_refund_and_net_bill_separation(self):
        tax = self.env['account.tax'].create({'name': 'Synthetic VAT13', 'amount': 13, 'type_tax_use': 'purchase'})
        bill = self._bill()
        bill.button_draft()
        bill.invoice_line_ids.write({'price_unit': 100, 'tax_ids': [Command.set(tax.ids)]})
        bill.action_post()
        blue = self._ticket(number='BLUE')
        blue.action_register()
        self._allocation(blue, bill, 100, 13).action_confirm()
        refund = bill._reverse_moves([{'date': '2026-09-02', 'invoice_date': '2026-09-02'}])
        refund.action_post()
        red = self._ticket(number='RED', color='red', original_id=blue.id, original_number=blue.number,
                           amount_untaxed=40, amount_tax=5.2)
        red.action_register()
        self._allocation(red, refund, 40, 5.2).action_confirm()
        self.assertEqual(refund.qing_fapiao_remaining_untaxed, 60)
        self.assertAlmostEqual(refund.qing_fapiao_remaining_tax, 7.8)
        self.assertEqual(bill.qing_fapiao_remaining_untaxed, 0)
        self.assertEqual(blue.remaining_untaxed, 0)
        self.assertEqual(blue.red_remaining_untaxed, 60)

    def test_allocation_batch_performance(self):
        for count in (50, 150):
            bill = self._bill()
            ticket = self._ticket(number='ALLOC-PERF-%s' % count, amount_untaxed=100, amount_tax=0)
            ticket.action_register()
            lines = self.env['qing.fapiao.allocation'].with_user(self.manager).create([
                {'fapiao_id': ticket.id, 'move_id': bill.id, 'amount_untaxed': .5, 'amount_tax': 0}
                for i in range(count)])
            start = self.cr.sql_log_count
            lines.action_confirm()
            queries = self.cr.sql_log_count - start
            _logger.info('CNF_ALLOCATION_PERF lines=%s confirmation_queries=%s', count, queries)
            self.assertLess(queries, 80)
            self.assertEqual(ticket.remaining_untaxed, 100 - count * .5)

    def test_registered_form_can_add_allocation(self):
        ticket = self._ticket(amount_tax=0)
        bill = self._bill()
        ticket.action_register()
        with Form(ticket) as form:
            with form.allocation_ids.new() as line:
                line.move_id = bill
                line.amount_untaxed = 50
        allocation = ticket.allocation_ids
        allocation.action_confirm()
        self.assertEqual(ticket.remaining_untaxed, 50)
