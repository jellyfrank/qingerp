from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import ReturnValuationCommon
from ..wizard import unbilled_purchase


@tagged('post_install', '-at_install')
class TestUnbilledPurchase(ReturnValuationCommon):
    def _wizard(self):
        return self.env['qing.unbilled.purchase'].create({'order_ids': [Command.set(self.po.ids)]})

    def test_draft_invoice_not_counted_and_read_only(self):
        self.bill.button_draft()
        wizard = self._wizard()
        before = (self.product.qty_available, self.product.total_value,
                  self.env['account.move'].search_count([('company_id', '=', self.company.id)]))
        row = wizard._rows()[0]
        self.assertEqual((row['received'], row['invoiced'], row['gap'], row['reference']), (10, 0, 10, 1000))
        wizard.action_generate()
        self.assertTrue(wizard.result_file)
        self.assertEqual(before, (self.product.qty_available, self.product.total_value,
                         self.env['account.move'].search_count([('company_id', '=', self.company.id)])))

    def test_partial_invoice_and_return_credit(self):
        self.bill.button_draft()
        self.bill.invoice_line_ids.quantity = 6
        self.bill.action_post()
        wizard = self._wizard()
        self.assertEqual(wizard._rows()[0]['gap'], 4)
        self._return()
        self.assertEqual(wizard._rows()[0]['gap'], 2)
        self._credit()
        row = wizard._rows()[0]
        self.assertEqual((row['received'], row['invoiced'], row['gap']), (8, 4, 4))

    def test_return_waiting_for_credit_and_balanced_filter(self):
        wizard = self._wizard()
        self.assertFalse(wizard._rows())
        self._return()
        row = wizard._rows()[0]
        self.assertEqual(row['gap'], -2)
        self.assertEqual(row['reference'], 0)
        self.assertIn('待贷项', row['status'])
        self._credit()
        self.assertFalse(wizard._rows())
        wizard.include_balanced = True
        self.assertEqual(wizard._rows()[0]['gap'], 0)

    def test_manager_native_source_access(self):
        manager = new_test_user(self.env, login='unbilled_manager', groups='account.group_account_manager',
                                company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        wizard = self.env['qing.unbilled.purchase'].with_user(manager).create({
            'order_ids': [Command.set(self.po.ids)], 'include_balanced': True})
        self.assertEqual(len(wizard._rows()), 1)
        wizard.action_generate()
        self.assertIn(self.po.name, wizard.result)
        user = new_test_user(self.env, login='unbilled_basic', groups='base.group_user')
        with self.assertRaises(AccessError):
            wizard.with_user(user).action_generate()

    def test_cross_company_rejected(self):
        wizard = self._wizard()
        other = self.env['res.company'].create({'name': 'Other unbilled company'})
        wizard.company_id = other
        with self.assertRaises(UserError):
            wizard.action_generate()

    def test_limit_and_stale_result(self):
        wizard = self._wizard()
        wizard.include_balanced = True
        wizard.action_generate()
        wizard.partner_ids = self.vendor
        self.assertFalse(wizard.result)
        self.assertFalse(wizard.result_file)
        with patch.object(unbilled_purchase, 'MAX_PURCHASE_LINES', 0), self.assertRaises(UserError):
            wizard.action_generate()

    def test_csv_and_html_escape(self):
        self.vendor.name = '=1+1'
        self.product.name = '<img src=x onerror=alert(1)>'
        wizard = self._wizard()
        wizard.include_balanced = True
        rows = wizard._rows()
        self.assertIn("'=1+1", wizard._csv(rows).decode('utf-8-sig'))
        rendered = str(wizard._render(rows))
        self.assertIn('&lt;img', rendered)
        self.assertNotIn('<img src=x', rendered)
        self.assertIn('/odoo/purchase.order/', rendered)
        self.assertIn('/odoo/stock.picking/', rendered)

    def test_invoice_unit_conversion(self):
        self.bill.button_draft()
        self.bill.invoice_line_ids.write({'product_uom_id': self.env.ref('uom.product_uom_dozen').id,
                                        'quantity': 0.5, 'price_unit': 1320})
        self.bill.action_post()
        row = self._wizard()._rows()[0]
        self.assertEqual(row['invoiced'], 6)
        self.assertEqual(row['gap'], 4)
