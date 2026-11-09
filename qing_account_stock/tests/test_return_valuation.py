from freezegun import freeze_time

from odoo import fields
from odoo.tests import tagged

from .common import ReturnValuationCommon


@tagged('post_install', '-at_install')
class TestReturnValuation(ReturnValuationCommon):
    def test_return_credit_preserves_remaining_value_and_reset(self):
        self._return()
        self.assertAlmostEqual(self.product.total_value, 880)
        credit = self._credit()
        self.assertAlmostEqual(self.product.total_value, 880)
        self.assertAlmostEqual(self.move.value, 1100)
        self.assertTrue(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))
        credit.button_draft()
        self.assertAlmostEqual(self.product.total_value, 880)
        credit.action_post()
        self.assertAlmostEqual(self.product.total_value, 880)
        # Historical cutoff before the credit retains gross original coverage.
        historical_move = self.move.with_context(at_date=fields.Date.to_date('2026-08-31'))
        self.assertAlmostEqual(historical_move._get_value_from_account_move(10)['value'], 1100)

    def test_credit_without_return_stays_native(self):
        credit = self._credit()
        self.assertFalse(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))
        self.assertAlmostEqual(self.move._get_quantity_from_bill(credit.invoice_line_ids, 10), 2)
        self.assertAlmostEqual(self.move._get_value_from_bill(credit.invoice_line_ids), 220)

    def test_price_adjustment_stays_native(self):
        self._return()
        credit = self._credit(price=105)
        self.assertFalse(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))
        self.assertAlmostEqual(self.move._get_value_from_bill(credit.invoice_line_ids), 210)

    def test_partial_credit_stays_native(self):
        self._return()
        credit = self._credit(quantity=1)
        self.assertFalse(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))
        self.assertAlmostEqual(self.move._get_quantity_from_bill(credit.invoice_line_ids, 10), 1)

    def test_full_return(self):
        self._return(quantity=10)
        credit = self._credit(quantity=10)
        self.assertTrue(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))
        self.assertAlmostEqual(self.product.qty_available, 0)
        self.assertAlmostEqual(self.product.total_value, 0)
        self.assertAlmostEqual(self.move.value, 1100)

    def test_multiple_receipts_stay_native(self):
        with freeze_time('2026-08-02'):
            other = self.receipt.copy()
            other.move_ids.write({'product_uom_qty': 2, 'purchase_line_id': self.po.order_line.id})
            other.action_confirm()
            other.action_assign()
            other.move_ids.quantity = 2
            other.move_ids.picked = True
            other.button_validate()
        self._return()
        credit = self._credit()
        self.assertFalse(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))
        self.assertAlmostEqual(self.move._get_quantity_from_bill(credit.invoice_line_ids, 10), 2)

    def test_unlinked_credit_stays_native(self):
        self._return()
        credit = self._credit()
        credit.button_draft()
        credit.reversed_entry_id = False
        credit.action_post()
        self.assertFalse(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))
        self.assertAlmostEqual(self.move._get_value_from_bill(credit.invoice_line_ids), 220)

    def test_multiple_credits_different_prices_stay_native(self):
        self._return(quantity=1)
        self._return(quantity=1)
        first = self._credit(quantity=1)
        second = self._credit(quantity=1, price=105)
        self.assertFalse(self.move._qing_is_physical_return_refund(first.invoice_line_ids))
        self.assertFalse(self.move._qing_is_physical_return_refund(second.invoice_line_ids))

    def test_credit_before_physical_return_stays_native(self):
        returned = self._return(quantity=2)
        returned.move_ids.date = '2026-09-03'
        credit = self._credit(quantity=2)
        self.assertFalse(self.move._qing_is_physical_return_refund(credit.invoice_line_ids))

    def test_unbilled_return_partial_billing_stays_native(self):
        self.bill.button_draft()
        self._return(quantity=2)
        self.bill.write({'name': '/', 'date': '2026-09-30', 'invoice_date': '2026-09-30'})
        self.bill.invoice_line_ids.quantity = 7
        self.bill.action_post()
        self.assertIsNone(self.move._qing_unbilled_return_value(10, False))

    def test_unbilled_return_prior_billing_stays_native(self):
        self.bill.button_draft()
        self.bill.invoice_line_ids.quantity = 8
        self.bill.action_post()
        self._return(quantity=2)
        self.assertIsNone(self.move._qing_unbilled_return_value(10, False))
