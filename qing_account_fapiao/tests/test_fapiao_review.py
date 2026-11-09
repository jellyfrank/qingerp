import logging

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, new_test_user, tagged

from . import test_fapiao


@tagged('post_install', '-at_install')
class TestFapiaoReview(AccountTestInvoicingCommon):
    _ticket = test_fapiao.TestFapiao._ticket
    _bill = test_fapiao.TestFapiao._bill
    _allocation = test_fapiao.TestFapiao._allocation

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='review_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='review_billing', groups='account.group_account_invoice',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])

    def _review(self, **values):
        return self.env['qing.fapiao.review'].with_user(self.manager).create({
            'date_from': '2026-09-01', 'date_to': '2026-09-30', 'partner_id': self.partner_a.id, **values})

    def _row(self, review, source):
        key = 'fapiao_id' if source._name == 'qing.fapiao' else 'move_id'
        return review.line_ids.filtered(lambda line: line[key] == source)

    def test_partial_confirmation_cancel_and_refresh(self):
        ticket = self._ticket(invoice_date='2026-09-01', amount_tax=0)
        bill = self._bill()
        review = self._review()
        review.action_check()
        self.assertEqual(self._row(review, ticket).issue, 'registration')
        self.assertEqual(self._row(review, bill).remaining_untaxed, 113)
        ticket.action_register()
        allocation = self._allocation(ticket, bill, 50)
        review.action_check()
        self.assertEqual(self._row(review, ticket).issue, 'confirmation')
        self.assertEqual(self._row(review, bill).pending_count, 1)
        allocation.action_confirm()
        review.action_check()
        self.assertEqual(self._row(review, ticket).remaining_untaxed, 50)
        self.assertEqual(self._row(review, bill).remaining_untaxed, 63)
        self.assertEqual(self._row(review, ticket).action_open_source()['res_id'], ticket.id)
        self.assertEqual(self._row(review, bill).action_open_source()['res_model'], 'account.move')
        allocation.cancel_reason = 'Synthetic correction'
        allocation.action_cancel()
        review.action_check()
        self.assertEqual(self._row(review, ticket).remaining_untaxed, 100)
        self.assertEqual(self._row(review, bill).remaining_untaxed, 113)
        ticket.cancel_reason = 'Synthetic cancellation'
        ticket.action_cancel_registration()
        review.action_check()
        self.assertFalse(self._row(review, ticket))

    def test_complete_allocation_and_out_of_period_ticket(self):
        bill = self._bill()
        ticket = self._ticket(invoice_date='2026-08-31', amount_untaxed=113, amount_tax=0)
        ticket.action_register()
        self._allocation(ticket, bill, 113).action_confirm()
        review = self._review()
        before = self.env['account.move'].search_count([])
        review.action_check()
        self.assertFalse(review.line_ids)
        self.assertEqual(review.source_count, 1)
        self.assertEqual(before, self.env['account.move'].search_count([]))
        self.assertTrue(review.generated_at)

    def test_tax_only_difference_and_legacy_red(self):
        ticket = self._ticket(invoice_date='2026-09-01')
        bill = self._bill()
        ticket.action_register()
        self._allocation(ticket, bill, 100).action_confirm()
        review = self._review()
        review.action_check()
        row = self._row(review, ticket)
        self.assertEqual(row.remaining_untaxed, 0)
        self.assertEqual(row.remaining_tax, 13)
        self.assertEqual(row.issue, 'ticket_balance')
        red = self._ticket(number='LEGACY', color='red', invoice_date='2026-09-02',
                           original_id=ticket.id, original_number=ticket.number, amount_untaxed=20, amount_tax=2)
        red.action_register()
        # Simulate pre-allocation legacy data, not a supported business write path.
        self.env.cr.execute('UPDATE qing_fapiao SET original_id=NULL WHERE id=%s', [red.id])
        red.invalidate_recordset()
        review.action_check()
        self.assertEqual(self._row(review, red).issue, 'legacy_red')

    def test_scope_direction_partner_and_dates(self):
        inside = self._ticket(number='IN', invoice_date='2026-09-30')
        outside = self._ticket(number='OLD', invoice_date='2026-08-31')
        output = self._ticket(number='OUT', direction='out', invoice_date='2026-09-01')
        unrelated = self._ticket(number='OTHER', partner_id=self.partner_b.id, invoice_date='2026-09-01')
        review = self._review(direction='in')
        review.action_check()
        self.assertTrue(self._row(review, inside))
        for record in (outside, output, unrelated):
            self.assertFalse(self._row(review, record))
        review.date_from = '2026-10-01'
        self.assertFalse(review.line_ids)
        self.assertFalse(review.generated_at)
        with self.assertRaises(UserError):
            review.action_check()

    def test_permissions_and_readonly_results(self):
        self._ticket(invoice_date='2026-09-01')
        review = self._review()
        review.action_check()
        row = review.line_ids[:1]
        other = new_test_user(self.env, login='other_review_manager', groups='account.group_account_manager')
        for user in (self.billing, other):
            with self.assertRaises(AccessError):
                row.with_user(user).read(['remaining_tax'])
            with self.assertRaises(AccessError):
                review.with_user(user).action_check()
        with self.assertRaises(AccessError):
            row.write({'remaining_tax': 0})
        with self.assertRaises(AccessError):
            review.write({'source_count': 0})
        with self.assertRaises(AccessError):
            self.env['qing.fapiao.review.line'].with_user(self.manager).with_context(fapiao_review_token=True).create({'review_id': review.id})
        another_company = self.env['res.company'].create({'name': 'Synthetic Review Company'})
        self.manager.company_ids = [Command.link(another_company.id)]
        with self.assertRaises(AccessError):
            row.with_context(allowed_company_ids=another_company.ids).action_open_source()
        with self.assertRaises(AccessError):
            self._review(line_ids=[])

    def test_overallocated_data_is_flagged_without_repair(self):
        ticket = self._ticket(invoice_date='2026-09-01', amount_tax=0)
        bill = self._bill()
        ticket.action_register()
        allocation = self._allocation(ticket, bill, 100)
        allocation.action_confirm()
        # Imported legacy corruption must be reported, not silently corrected.
        self.env.cr.execute('UPDATE qing_fapiao_allocation SET amount_untaxed=120 WHERE id=%s', [allocation.id])
        allocation.invalidate_recordset()
        review = self._review()
        review.action_check()
        self.assertEqual(self._row(review, ticket).issue, 'overallocated')
        self.assertEqual(self._row(review, bill).remaining_untaxed, -7)
        self.assertEqual(allocation.amount_untaxed, 120)

    def test_form_and_batch_query_scaling(self):
        for count in (50, 150):
            tickets = self.env['qing.fapiao']
            for i in range(count):
                tickets |= self._ticket(number='BATCH-%s-%s' % (count, i), invoice_date='2026-09-01')
            with Form(self.env['qing.fapiao.review'].with_user(self.manager)) as form:
                form.date_from = '2026-09-01'
                form.date_to = '2026-09-30'
                form.partner_id = self.partner_a
            review = form.record
            start = self.cr.sql_log_count
            review.action_check()
            queries = self.cr.sql_log_count - start
            logging.getLogger(__name__).info('CNF20_REVIEW_PERF sources=%s queries=%s', count, queries)
            self.assertLess(queries, 90)
            self.assertTrue(set(tickets.ids).issubset(review.line_ids.fapiao_id.ids))
            tickets.unlink()
