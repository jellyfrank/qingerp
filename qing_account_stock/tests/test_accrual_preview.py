from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import ReturnValuationCommon


@tagged('post_install', '-at_install')
class TestAccrualPreview(ReturnValuationCommon):
    def _preview(self):
        self.bill.button_draft()
        wizard = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(self.po.ids)]})
        batch = self.env['qing.accrual.batch']._from_wizard(wizard)
        batch.action_lock()
        account = self.env['account.account'].create({'name': 'Accrued purchases', 'code': '299901',
            'account_type': 'liability_current', 'company_ids': [Command.set(self.company.ids)]})
        return self.env['qing.accrual.preview'].create({'batch_id': batch.id, 'liability_account_id': account.id})

    def test_preview_is_balanced_and_does_not_write_accounting(self):
        preview = self._preview()
        count = self.env['account.move'].search_count([])
        preview.action_preview()
        rows = preview._rows()
        self.assertEqual(sum(row['amount'] for row in rows), 1000)
        self.assertNotEqual(rows[0]['debit'], self.company.account_stock_valuation_id)
        self.assertIn('1000.00', preview.result)
        self.assertEqual(count, self.env['account.move'].search_count([]))
        preview.write({'liability_account_id': preview.liability_account_id.id})
        self.assertFalse(preview.result)

    def test_changed_sources_and_cancel_block_preview(self):
        preview = self._preview()
        self.bill.action_post()
        with self.assertRaises(UserError):
            preview.action_preview()
        preview.batch_id.action_cancel()
        with self.assertRaises(UserError):
            preview.action_preview()

    def test_wrong_account_rejected(self):
        preview = self._preview()
        preview.liability_account_id = self.company.account_stock_valuation_id
        with self.assertRaises(UserError):
            preview.action_preview()

    def test_foreign_currency_rejected(self):
        preview = self._preview()
        preview.batch_id.action_cancel()
        self.po.currency_id = self.env.ref('base.USD')
        wizard = self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(self.po.ids)]})
        batch = self.env['qing.accrual.batch']._from_wizard(wizard)
        batch.action_lock()
        preview.batch_id = batch
        with self.assertRaises(UserError):
            preview.action_preview()

    def test_native_close_plus_proposed_entry_preserves_stock_and_liability(self):
        preview = self._preview()
        # Posting only in rollback test fixtures; the product exposes no posting action.
        for mode in ('periodic', 'real_time'):
            with self.subTest(mode=mode), self.env.cr.savepoint():
                self.company.inventory_valuation = mode
                stock_value = self.product.total_value
                rows = preview._rows()
                vals = []
                for row in rows:
                    vals.extend([Command.create({'account_id': row['debit'].id, 'debit': row['amount']}),
                                 Command.create({'account_id': row['credit'].id, 'credit': row['amount'],
                                                 'partner_id': self.vendor.id})])
                proposed = self.env['account.move'].create({'move_type': 'entry',
                    'journal_id': self.company.account_stock_journal_id.id,
                    'date': fields.Date.today(), 'line_ids': vals})
                proposed.action_post()
                closing = self.company.action_close_stock_valuation(auto_post=True)
                domain = [('company_id', '=', self.company.id), ('parent_state', '=', 'posted')]
                lines = self.env['account.move.line'].search(domain)
                self.assertEqual(sum(lines.filtered(lambda l: l.account_id == self.company.account_stock_valuation_id).mapped('balance')), stock_value)
                self.assertEqual(sum(lines.filtered(lambda l: l.account_id == preview.liability_account_id).mapped('balance')), -1000)
                self.env['account.move'].browse(closing['res_id']).button_draft()
                self.env['account.move'].browse(closing['res_id']).unlink()
                proposed.button_draft()
                proposed.unlink()

    def test_permissions_and_cross_company_account(self):
        preview = self._preview()
        basic = new_test_user(self.env, login='preview_basic', groups='base.group_user')
        with self.assertRaises(AccessError):
            preview.with_user(basic).action_preview()
        manager = new_test_user(self.env, login='preview_manager', groups='account.group_account_manager',
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        own_preview = self.env['qing.accrual.preview'].with_user(manager).create({
            'batch_id': preview.batch_id.id, 'liability_account_id': preview.liability_account_id.id})
        own_preview.action_preview()
        self.assertIn('1000.00', own_preview.result)
        other = self.env['res.company'].create({'name': 'Other preview company'})
        preview.liability_account_id = self.env['account.account'].create({
            'name': 'Other liability', 'code': '299902', 'account_type': 'liability_current',
            'company_ids': [Command.set(other.ids)]})
        with self.assertRaises(UserError):
            preview.action_preview()
