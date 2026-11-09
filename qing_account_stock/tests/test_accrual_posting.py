from datetime import timedelta

from freezegun import freeze_time
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import ReturnValuationCommon
from .test_accrual_draft import TestAccrualDraft


@tagged('post_install', '-at_install')
class TestAccrualPosting(ReturnValuationCommon):
    _wizard = TestAccrualDraft._wizard

    def _batch(self):
        wizard = self._wizard()
        wizard.action_create_draft()
        return wizard.batch_id

    def test_post_reverse_and_protection(self):
        batch = self._batch()
        batch.action_post()
        self.assertEqual(batch.state, 'posted')
        self.assertEqual(batch.draft_move_id.state, 'posted')
        self.assertEqual(batch.posted_by, self.env.user)
        with self.assertRaises(UserError):
            batch.action_reverse()  # Default next-month date is not due yet.
        for action in (batch.action_post, batch.action_cancel):
            with self.assertRaises(UserError):
                action()
        batch.reversal_date = fields.Date.context_today(batch)
        batch.action_reverse()
        reversal = batch.reversal_move_id
        self.assertEqual(batch.state, 'reversed')
        self.assertEqual(reversal.state, 'posted')
        self.assertEqual(reversal.reversed_entry_id, batch.draft_move_id)
        self.assertEqual(reversal.qing_accrual_reversal_batch_id, batch)
        balances = {}
        for line in (batch.draft_move_id | reversal).line_ids:
            key = (line.account_id.id, line.partner_id.id)
            balances[key] = balances.get(key, 0) + line.balance
        self.assertTrue(all(value == 0 for value in balances.values()))
        self.assertFalse(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)]))
        for move in batch.draft_move_id | reversal:
            for action in (move.button_draft, move.unlink, move.copy,
                           lambda: move.line_ids.write({'name': 'tamper'})):
                with self.assertRaises(UserError):
                    action()
        with self.assertRaises(UserError):
            batch.action_reverse()

    def test_source_changed_before_post(self):
        batch = self._batch()
        self.bill.action_post()
        with self.assertRaises(UserError):
            batch.action_post()
        self.assertEqual(batch.state, 'prepared')
        self.assertEqual(batch.draft_move_id.state, 'draft')

    def test_reverse_preserves_original_after_invoice(self):
        batch = self._batch()
        batch.action_post()
        self.bill.action_post()  # Actual invoice 1100 differs from original accrual 1000.
        batch.reversal_date = fields.Date.context_today(batch)
        batch.action_reverse()
        self.assertEqual(sum(batch.reversal_move_id.line_ids.mapped('credit')), -1000)

    def test_lock_dates_and_atomicity(self):
        batch = self._batch()
        today = fields.Date.context_today(batch)
        self.company.fiscalyear_lock_date = today
        with self.assertRaises(UserError):
            batch.action_post()
        self.assertEqual(batch.draft_move_id.state, 'draft')
        self.company.fiscalyear_lock_date = False
        batch.action_post()
        batch.reversal_date = today
        self.company.fiscalyear_lock_date = today
        with self.assertRaises(UserError):
            batch.action_reverse()
        self.assertEqual(batch.state, 'posted')
        self.assertFalse(batch.reversal_move_id)
        self.assertTrue(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)]))

    def test_dates(self):
        batch = self._batch()
        today = fields.Date.context_today(batch)
        with freeze_time(today + timedelta(days=1)):
            with self.assertRaises(UserError):
                batch.action_post()
        batch.action_post()
        batch.reversal_date = today - timedelta(days=1)
        with self.assertRaises(UserError):
            batch.action_reverse()

    def test_manager_and_basic_user(self):
        batch = self._batch()
        basic = new_test_user(self.env, login='post_basic', groups='base.group_user',
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        with self.assertRaises(AccessError):
            batch.with_user(basic).action_post()
        manager = new_test_user(self.env, login='post_manager', groups='account.group_account_manager',
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        own = batch.with_user(manager)
        own.action_post()
        own.reversal_date = fields.Date.context_today(own)
        own.action_reverse()
        self.assertEqual(own.posted_by, manager)
        self.assertEqual(own.reversed_by, manager)
        self.assertEqual(own.reversal_move_id.create_uid, manager)

    def test_cross_month_controlled_cycle(self):
        with freeze_time('2026-08-31'):
            batch = self._batch()
            batch.action_post()
            self.assertEqual(str(batch.reversal_date), '2026-09-01')
        with freeze_time('2026-09-01'):
            batch.action_reverse()
            self.assertEqual(str(batch.reversal_move_id.date), '2026-09-01')
            self.assertEqual(sum(batch.reversal_move_id.line_ids.mapped('debit')), -1000)
            self.bill.action_post()
            source = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(self.po.ids)]})
            with self.assertRaises(UserError):
                self.env['qing.accrual.batch']._from_wizard(source)
