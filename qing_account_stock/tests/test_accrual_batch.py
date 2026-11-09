from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import ReturnValuationCommon


@tagged('post_install', '-at_install')
class TestAccrualBatch(ReturnValuationCommon):
    def _batch(self):
        if self.bill.state != 'draft':
            self.bill.button_draft()
        wizard = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(self.po.ids)]})
        return self.env['qing.accrual.batch']._from_wizard(wizard)

    def test_capture_lock_cancel_and_reuse(self):
        first = self._batch()
        second = self._batch()
        self.assertEqual(first.state, 'draft')
        first.action_lock()
        self.assertEqual(first.state, 'locked')
        with self.assertRaises(UserError):
            second.action_lock()
        first.action_cancel()
        second.action_lock()
        self.assertEqual(second.state, 'locked')
        self.assertEqual(first.state, 'cancel')

    def test_source_changes_reject_lock_and_preserve_snapshot(self):
        batch = self._batch()
        original = batch.snapshot_html
        self.bill.invoice_line_ids.quantity = 6
        self.bill.action_post()
        with self.assertRaises(UserError):
            batch.action_lock()
        self.assertEqual(batch.snapshot_html, original)
        self.assertEqual(batch.action_check_sources()['params']['type'], 'warning')

    def test_locked_source_changes_are_reported(self):
        batch = self._batch()
        batch.action_lock()
        self.bill.action_post()
        self.assertEqual(batch.state, 'locked')
        self.assertEqual(batch.action_check_sources()['params']['type'], 'warning')
        self.assertEqual(len(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)])), 1)
        batch.action_cancel()
        self.assertFalse(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)]))

    def test_direct_mutation_and_delete_guards(self):
        batch = self._batch()
        with self.assertRaises(UserError):
            self.env['qing.accrual.batch'].create({'name': 'Forged'})
        with self.assertRaises(UserError):
            batch.write({'state': 'locked'})
        batch.notes = 'Review this batch'
        batch.action_lock()
        claim = self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)])
        with self.assertRaises(UserError):
            claim.unlink()
        with self.assertRaises(UserError):
            batch.notes = 'Overwrite'
        with self.assertRaises(UserError):
            batch.unlink()
        batch.action_cancel()
        with self.assertRaises(UserError):
            batch.unlink()

    def test_no_business_moves_created(self):
        batch = self._batch()
        domain = [('company_id', '=', self.company.id)]
        count = self.env['account.move'].search_count(domain)
        quantity = self.product.qty_available
        batch.action_lock()
        batch.action_cancel()
        self.assertEqual(count, self.env['account.move'].search_count(domain))
        self.assertEqual(quantity, self.product.qty_available)

    def test_empty_and_negative_report_rejected(self):
        wizard = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(self.po.ids)]})
        with self.assertRaises(UserError):
            self.env['qing.accrual.batch']._from_wizard(wizard)
        self._return()
        with self.assertRaises(UserError):
            self.env['qing.accrual.batch']._from_wizard(wizard)

    def test_manager_and_company_guards(self):
        batch = self._batch()
        user = new_test_user(self.env, login='batch_basic', groups='base.group_user')
        with self.assertRaises(AccessError):
            batch.with_user(user).action_lock()
        other = self.env['res.company'].create({'name': 'Other batch company'})
        with self.assertRaises(UserError):
            batch.with_company(other).action_lock()
        manager = new_test_user(self.env, login='batch_manager', groups='account.group_account_manager',
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        batch.with_user(manager).action_lock()
        self.assertEqual(batch.locked_by, manager)

    def test_database_excludes_duplicate_claims(self):
        batch = self._batch()
        batch.action_lock()
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env.cr.execute("""INSERT INTO qing_accrual_source
                (batch_id, company_id, purchase_line_id) VALUES (%s, %s, %s)""",
                (batch.id, batch.company_id.id, batch.source_line_ids.id))
