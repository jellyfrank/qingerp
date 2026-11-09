from odoo import fields, models
from odoo.tools.float_utils import float_compare


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _qing_is_physical_return_refund(self, line):
        """Recognize an unambiguous, fully invoiced same-price physical return.

        Keep the original receipt's gross invoice coverage: the outgoing return
        already removes the goods from inventory. Subtracting its credit again
        from coverage makes native valuation fall back to the older PO price.
        Ambiguous, foreign-currency and price-adjustment cases stay native.
        """
        self.ensure_one()
        if (not self.is_in or self.origin_returned_move_id or not self.purchase_line_id
                or line.move_type != 'in_refund' or line.move_id.state != 'posted'
                or not line.move_id.reversed_entry_id):
            return False
        purchase = self.purchase_line_id
        cutoff = self.env.context.get('_qing_valuation_at_date')
        moves = purchase.move_ids.filtered(lambda m: m.state == 'done' and m.product_id == self.product_id
            and (not cutoff or m.date.date() <= cutoff))
        receipts = moves.filtered(lambda m: m.is_in)
        returns = moves.filtered(lambda m: m.is_out)
        if self not in receipts or not returns or any(
                m.origin_returned_move_id not in receipts or not m.to_refund for m in returns):
            return False
        lines = purchase.invoice_lines.filtered(lambda l: l.move_id.state == 'posted'
            and (not cutoff or l.date <= cutoff))
        bills = lines.filtered(lambda l: l.move_type == 'in_invoice')
        refunds = lines.filtered(lambda l: l.move_type == 'in_refund')
        if not bills or not refunds or line not in refunds or any(
                l.currency_id != self.company_id.currency_id or l.quantity <= 0
                or l.product_id != self.product_id or l.company_id != self.company_id
                for l in bills | refunds):
            return False
        # A shared price is required; no allocation of differently priced batches.
        prices = [l.price_subtotal / l.product_uom_id._compute_quantity(
            l.quantity, self.product_id.uom_id, round=False) for l in bills | refunds]
        if any(float_compare(price, prices[0], precision_digits=6) for price in prices):
            return False
        for refund in refunds:
            original = refund.move_id.reversed_entry_id
            if (original not in bills.move_id or original.state != 'posted'
                    or original.date > refund.date):
                return False
            # Never use later physical returns to justify an earlier credit.
            due_returns = returns.filtered(lambda m: m.date.date() <= refund.date)
            due_refunds = refunds.filtered(lambda l: l.date <= refund.date)
            uom = self.product_id.uom_id
            physical = sum(m.uom_id._compute_quantity(m.quantity, uom, round=False) for m in due_returns)
            credited = sum(l.product_uom_id._compute_quantity(l.quantity, uom, round=False) for l in due_refunds)
            if uom.compare(credited, physical) > 0:
                return False
        uom = self.product_id.uom_id
        billed_qty = sum(l.product_uom_id._compute_quantity(l.quantity, uom, round=False) for l in bills)
        refunded_qty = sum(l.product_uom_id._compute_quantity(l.quantity, uom, round=False) for l in refunds)
        received_qty = sum(m.uom_id._compute_quantity(m.quantity, uom, round=False) for m in receipts)
        for receipt in receipts:
            quantity = sum(m.uom_id._compute_quantity(m.quantity, uom, round=False)
                for m in returns if m.origin_returned_move_id == receipt)
            if uom.compare(quantity, receipt.uom_id._compute_quantity(receipt.quantity, uom, round=False)) > 0:
                return False
        returned_qty = sum(m.uom_id._compute_quantity(m.quantity, uom, round=False) for m in returns)
        return (uom.compare(billed_qty, received_qty) == 0
                and uom.compare(refunded_qty, returned_qty) == 0
                and uom.compare(returned_qty, received_qty) <= 0)

    def _qing_unbilled_return_value(self, quantity, cutoff):
        """Value retained goods after an unbilled return, without inventing a credit.

        Only a single original receipt/return and first billing strictly after
        the return are supported. Uniform full net billing determines the unit
        cost of all remaining goods; raw receipt quantity must not dilute it.
        """
        self.ensure_one()
        if not self.is_in or self.origin_returned_move_id or not self.purchase_line_id:
            return None
        purchase = self.purchase_line_id
        moves = purchase.move_ids.filtered(lambda m: m.state == 'done'
            and (not cutoff or m.date.date() <= cutoff))
        receipts = moves.filtered(lambda m: m.is_in)
        returns = moves.filtered(lambda m: m.is_out)
        if receipts != self or len(returns) != 1 or returns.origin_returned_move_id != self or not returns.to_refund:
            return None
        bills = purchase.invoice_lines.filtered(lambda l: l.move_id.state == 'posted'
            and (not cutoff or l.date <= cutoff))
        if not bills or any(l.move_type != 'in_invoice' or l.quantity <= 0
                or l.product_id != self.product_id or l.company_id != self.company_id
                or l.currency_id != self.company_id.currency_id or l.date <= returns.date.date()
                for l in bills):
            return None
        uom = self.product_id.uom_id
        received = self.uom_id._compute_quantity(self.quantity, uom, round=False)
        returned = returns.uom_id._compute_quantity(returns.quantity, uom, round=False)
        billed = sum(l.product_uom_id._compute_quantity(l.quantity, uom, round=False) for l in bills)
        if uom.compare(returned, 0) <= 0 or uom.compare(returned, received) >= 0 or uom.compare(billed, received - returned):
            return None
        prices = [l.price_subtotal / l.product_uom_id._compute_quantity(l.quantity, uom, round=False) for l in bills]
        if any(float_compare(price, prices[0], precision_digits=6) for price in prices):
            return None
        return {'quantity': quantity, 'value': self.company_currency_id.round(quantity * prices[0]),
            'description': self.env._('未到票退货后，剩余数量全部按同价到票。')}

    def _qing_historical_account_value(self, quantity, cutoff):
        """Apply Odoo 20 purchase valuation to documents available at cutoff."""
        valuation_data = {'quantity': 0.0, 'value': 0.0, 'description': ''}
        if not self.purchase_line_id:
            return valuation_data
        aml_quantity = 0.0
        value = 0.0
        aml_ids = set()
        invoice_lines = self.purchase_line_id.invoice_lines.filtered(
            lambda line: line.move_id.state == 'posted' and line.date <= cutoff
        )
        for line in invoice_lines:
            aml_ids.add(line.id)
            if line.move_type == 'in_invoice':
                aml_quantity += self._get_quantity_from_bill(line, quantity)
                value += self._get_value_from_bill(line)
            elif line.move_type == 'in_refund':
                aml_quantity -= self._get_quantity_from_bill(line, quantity)
                value -= self._get_value_from_bill(line)
        if aml_quantity <= 0:
            return valuation_data

        other_candidates_qty = 0.0
        for move in self.purchase_line_id.move_ids:
            if (move == self or move.product_id != self.product_id
                    or move.date.date() > cutoff
                    or move.date > self.date
                    or (move.date == self.date and move.id > self.id)):
                continue
            if move.is_in or move.is_dropship:
                other_candidates_qty += move._get_valued_qty()
            elif move.is_out:
                other_candidates_qty -= -move._get_valued_qty()
        if self.uom_id.compare(aml_quantity, other_candidates_qty) <= 0:
            return valuation_data

        value *= (aml_quantity - other_candidates_qty) / aml_quantity
        aml_quantity -= other_candidates_qty
        if quantity >= aml_quantity:
            valuation_data.update(quantity=aml_quantity, value=value)
        else:
            valuation_data.update(quantity=quantity, value=quantity * value / aml_quantity)
        bills = self.env['account.move.line'].browse(aml_ids).move_id
        valuation_data['description'] = self.env._(
            '%(value)s for %(quantity)s %(unit)s from %(bills)s',
            value=self.company_currency_id.format(value),
            quantity=aml_quantity,
            unit=self.product_id.uom_id.name,
            bills=bills.mapped('display_name'),
        )
        return valuation_data

    def _get_value_from_account_move(self, quantity):
        cutoff_value = (
            self.env.context.get("_qing_valuation_at_date")
            or self.env.context.get("at_date")
            or self.env.context.get("to_date")
        )
        cutoff = fields.Date.to_date(cutoff_value) if cutoff_value else False
        net_value = self._qing_unbilled_return_value(quantity, cutoff)
        if net_value is not None:
            return net_value
        if cutoff:
            return self.with_context(
                _qing_valuation_at_date=cutoff
            )._qing_historical_account_value(quantity, cutoff)
        return super()._get_value_from_account_move(quantity)

    def _get_value_from_bill(self, aml):
        if self._qing_is_physical_return_refund(aml):
            return 0.0
        return super()._get_value_from_bill(aml)

    def _get_quantity_from_bill(self, aml, quantity):
        if self._qing_is_physical_return_refund(aml):
            return 0.0
        return super()._get_quantity_from_bill(aml, quantity)
