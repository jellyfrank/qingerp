from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .common import ReturnValuationCommon


@tagged('post_install', '-at_install')
class TestAccrualDraft(ReturnValuationCommon):
    def _wizard(self):
        self.bill.button_draft()
        source = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(self.po.ids)]})
        batch = self.env['qing.accrual.batch']._from_wizard(source)
        batch.action_lock()
        liability = self.env['account.account'].create({'name': 'Draft accrual', 'code': '299904',
            'account_type': 'liability_current', 'company_ids': [Command.set(self.company.ids)]})
        return self.env['qing.accrual.preview'].create({'batch_id': batch.id,
            'liability_account_id': liability.id, 'journal_id': self.company.account_stock_journal_id.id})

    def test_generate_and_cancel(self):
        wizard = self._wizard()
        wizard.action_create_draft()
        batch = wizard.batch_id
        move = batch.draft_move_id
        self.assertEqual(move.state, 'draft')
        self.assertEqual(move.qing_accrual_batch_id, batch)
        self.assertEqual(batch.state, 'prepared')
        self.assertEqual(sum(move.line_ids.mapped('debit')), 1000)
        self.assertEqual(sum(move.line_ids.mapped('credit')), 1000)
        snapshot = batch.entry_snapshot
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        batch.action_cancel()
        self.assertFalse(move.exists())
        self.assertEqual(batch.entry_snapshot, snapshot)
        self.assertFalse(self.env['qing.accrual.source'].search([('batch_id', '=', batch.id)]))

    def test_protected_move_and_lines(self):
        wizard = self._wizard()
        wizard.action_create_draft()
        move = wizard.batch_id.draft_move_id
        for action in [lambda: move.write({'ref': 'changed'}), lambda: move.unlink(),
                       lambda: move.copy(), lambda: move.action_post(),
                       lambda: move.line_ids.write({'name': 'changed'}), lambda: move.line_ids.unlink(),
                       lambda: move.with_context(_qing_accrual_token=True).write({'ref': 'forged'}),
                       lambda: self.env['account.move.line'].create({'move_id': move.id,
                           'account_id': move.line_ids[0].account_id.id, 'debit': 1})]:
            with self.assertRaises(UserError):
                action()
        self.assertEqual(move.state, 'draft')

    def test_date_and_journal_validation(self):
        wizard = self._wizard()
        wizard.entry_date = fields.Date.context_today(wizard) - timedelta(days=1)
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        wizard.entry_date = fields.Date.context_today(wizard)
        wizard.journal_id = False
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertFalse(wizard.batch_id.draft_move_id)

    def test_source_changed_blocks_generation(self):
        wizard = self._wizard()
        self.bill.action_post()
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertFalse(wizard.batch_id.draft_move_id)

    def test_direct_link_cannot_be_forged(self):
        wizard = self._wizard()
        with self.assertRaises(UserError):
            self.env['account.move'].create({'qing_accrual_batch_id': wizard.batch_id.id})
        with self.assertRaises(UserError):
            self.bill.write({'qing_accrual_batch_id': wizard.batch_id.id})

    def test_native_lock_date(self):
        wizard = self._wizard()
        self.company.fiscalyear_lock_date = fields.Date.context_today(wizard)
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertFalse(wizard.batch_id.draft_move_id)

    def test_finance_manager_can_generate_and_cancel(self):
        wizard = self._wizard()
        manager = new_test_user(self.env, login='draft_manager', groups='account.group_account_manager',
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        own = self.env['qing.accrual.preview'].with_user(manager).create({
            'batch_id': wizard.batch_id.id, 'liability_account_id': wizard.liability_account_id.id,
            'journal_id': wizard.journal_id.id})
        own.action_create_draft()
        self.assertEqual(own.batch_id.draft_move_id.create_uid, manager)
        own.batch_id.action_cancel()
        self.assertEqual(own.batch_id.state, 'cancel')
