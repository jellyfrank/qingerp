from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import ReturnValuationCommon
from .test_accrual_draft import TestAccrualDraft


@tagged('post_install', '-at_install')
class TestAccrualClosing(ReturnValuationCommon):
    _wizard = TestAccrualDraft._wizard

    def test_due_today_and_future(self):
        wizard = self._wizard()
        wizard.action_create_draft()
        batch = wizard.batch_id
        batch.action_post()
        check = self.env['qing.accrual.closing'].create({})
        self.assertFalse(check._issues()[1])
        batch.reversal_date = fields.Date.context_today(batch)
        self.assertEqual(len(check._issues()[1]), 1)
        check.action_check()
        self.assertIn(batch.name, check.result)
        self.assertEqual(batch.state, 'posted')
        batch.action_reverse()
        self.assertFalse(check._issues()[1])

    def test_pending_and_cancelled(self):
        wizard = self._wizard()
        check = self.env['qing.accrual.closing'].create({})
        self.assertEqual(len(check._issues()[1]), 1)
        wizard.action_create_draft()
        self.assertEqual(len(check._issues()[1]), 1)
        wizard.batch_id.action_cancel()
        self.assertFalse(check._issues()[0])

    def test_missing_and_invalid_date(self):
        wizard = self._wizard()
        wizard.action_create_draft()
        batch = wizard.batch_id
        batch.action_post()
        check = self.env['qing.accrual.closing'].create({})
        batch.reversal_date = False
        self.assertIn('缺少', check._issues()[1][0][1])
        batch.reversal_date = batch.draft_move_id.date - timedelta(days=1)
        self.assertIn('早于', check._issues()[1][0][1])

    def test_scope_and_permissions(self):
        wizard = self._wizard()
        manager = new_test_user(self.env, login='closing_manager', groups='account.group_account_manager',
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        check = self.env['qing.accrual.closing'].with_user(manager).create({})
        self.assertEqual(check._issues()[0], wizard.batch_id)
        other = self.env['res.company'].create({'name': 'Other closing company'})
        check.company_id = other
        with self.assertRaises(UserError):
            check.action_check()
        basic = new_test_user(self.env, login='closing_basic', groups='base.group_user',
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        with self.assertRaises(AccessError):
            self.env['qing.accrual.closing'].with_user(basic).create({})

    def test_result_links_and_refresh(self):
        wizard = self._wizard()
        check = self.env['qing.accrual.closing'].create({})
        check.action_check()
        self.assertIn('/odoo/qing.accrual.batch/', check.result)
        wizard.batch_id.action_cancel()
        check.action_check()
        self.assertNotIn('/odoo/qing.accrual.batch/', check.result)
        self.assertIn('不代表整个账套', check.result)
