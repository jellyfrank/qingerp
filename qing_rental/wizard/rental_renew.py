from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_datetime

from ..models.common import check_operator, internal, periods


class RentalRenew(models.TransientModel):
    _name = 'qing.rental.renew'
    _description = 'Renew rental'

    @api.depends('new_end', 'order_id')
    def _compute_charge(self):
        for wizard in self:
            wizard.charge_amount = sum(quantity * price * (1 - wizard._discount(line) / 100) for line, quantity, price in wizard._charges())

    def _discount(self, line):
        charge = line.order_id.order_line.filtered(lambda row: row.qing_rental_line_id == line and row.qing_rental_charge == 'rent')
        return charge.discount if len(charge) == 1 else 0

    def _charges(self):
        self.ensure_one()
        order = self.order_id
        if not self.new_end or not order.qing_rental_end or self.new_end <= order.qing_rental_end:
            return []
        result = []
        for line in order.qing_rental_line_ids:
            quantity = max(0, line.quantity - line.qty_returned)
            old_periods = periods(order.qing_rental_start, order.qing_rental_end, line.period)
            new_periods = periods(order.qing_rental_start, self.new_end, line.period)
            result.append((line, quantity, (new_periods - old_periods) * line.rate))
        return result

    charge_amount = fields.Monetary(compute='_compute_charge')
    currency_id = fields.Many2one(related='order_id.currency_id')
    new_end = fields.Datetime(required=True, string='New return date')
    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade')

    def action_apply(self):
        self.ensure_one()
        self.check_access('write')
        check_operator(self.env)
        order = self.order_id
        order._qing_rental_lock()
        if order.state != 'sale' or not order.is_qing_rental:
            raise UserError(self.env._('Only confirmed rentals can be renewed.'))
        if not self.new_end or self.new_end <= order.qing_rental_end:
            raise UserError(self.env._('The new return date must be later than the current return date.'))
        lines = order.qing_rental_line_ids
        lines.product_id._lock_rental_inventory()
        if not any(line.quantity > line.qty_returned for line in lines):
            raise UserError(self.env._('Fully returned rentals cannot be renewed.'))
        order._qing_rental_check_capacity(end=self.new_end)
        old_end = order.qing_rental_end
        SaleLine = internal(self.env['sale.order.line'])
        # Append the incremental charge; never rewrite an invoiced rental charge.
        if order.locked:
            raise UserError(self.env._('Unlock the sales order before renewing it.'))
        for line, quantity, price in self._charges():
            if not quantity or not price:
                continue
            SaleLine.create({'order_id': order.id, 'product_id': line.service_product_id.id,
                'product_uom_id': line.service_product_id.uom_id.id, 'product_uom_qty': quantity,
                'price_unit': price, 'discount': self._discount(line), 'qing_rental_line_id': line.id, 'qing_rental_charge': 'renewal',
                'name': self.env._('%(equipment)s — Renewal: %(start)s → %(end)s',
                                   equipment=line.product_id.display_name, start=format_datetime(self.env, old_end), end=format_datetime(self.env, self.new_end))})
        internal(order).write({'qing_rental_end': self.new_end})
        order.message_post(body=self.env._('Rental extended from %(old)s to %(new)s.', old=old_end, new=self.new_end))
        return {'type': 'ir.actions.act_window_close'}
