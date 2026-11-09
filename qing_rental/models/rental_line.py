from collections import defaultdict
from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import PERIODS, check_operator, finite_nonnegative, is_internal, periods


EDITABLE_FIELDS = {'order_id', 'product_id', 'service_product_id', 'quantity', 'period', 'rate', 'padding_hours', 'sequence'}


class RentalLine(models.Model):
    _name = 'qing.rental.line'
    _description = 'Rental equipment'
    _order = 'sequence, id'
    _check_company_auto = True

    def _check_editable(self):
        check_operator(self.env)
        self.order_id.check_access('write')
        if not is_internal(self.env) and any(order.state not in ('draft', 'sent') for order in self.order_id):
            raise UserError(self.env._('Confirmed rental equipment cannot be changed. Use renewal or cancel before pickup.'))

    @api.depends('quantity', 'rate', 'period', 'order_id.qing_rental_start', 'order_id.qing_rental_end',
                 'order_id.state', 'sales_line_ids.price_subtotal', 'sales_line_ids.qing_rental_charge')
    def _compute_amount(self):
        totals = defaultdict(float)
        confirmed = self.filtered(lambda line: line.order_id.state not in ('draft', 'sent'))
        # Read all linked charges together under the caller's normal sale access.
        # price_subtotal is the native untaxed amount after discounts and taxes.
        charges = confirmed.sales_line_ids.filtered(lambda charge: charge.qing_rental_charge in ('rent', 'renewal'))
        for charge in charges:
            totals[charge.qing_rental_line_id.id] += charge.price_subtotal
        for line in self:
            if line.order_id.state not in ('draft', 'sent'):
                line.amount = totals[line.id]
                continue
            start, end = line.order_id.qing_rental_start, line.order_id.qing_rental_end
            line.amount = line.quantity * line.rate * periods(start, end, line.period) if start and end else 0

    @api.depends('move_ids.state', 'move_ids.quantity', 'move_ids.qing_rental_direction')
    def _compute_quantities(self):
        totals = defaultdict(float)
        domain = [('qing_rental_line_id', 'in', self.ids), ('state', '=', 'done')]
        # Only numeric totals of already-authorized lines are exposed to sales staff.
        Move = self.env['stock.move'].sudo()
        for line, direction, qty in Move._read_group(domain, ['qing_rental_line_id', 'qing_rental_direction'], ['quantity:sum']):
            totals[line.id, direction] = qty
        for line in self:
            line.qty_out = totals[line.id, 'out']
            line.qty_returned = totals[line.id, 'in']

    @api.depends('order_id.state', 'order_id.qing_rental_start', 'order_id.qing_rental_end',
                 'order_id.company_id', 'order_id.warehouse_id',
                 'order_id.warehouse_id.qing_rental_stock_location_id',
                 'order_id.warehouse_id.qing_rental_out_location_id',
                 'order_id.qing_rental_line_ids.product_id', 'order_id.qing_rental_line_ids.quantity',
                 'order_id.qing_rental_line_ids.padding_hours', 'order_id.qing_rental_line_ids.qty_returned')
    def _compute_available(self):
        for line in self:
            line.available_qty = 0
            line.required_qty = 0
            line.availability_state = False
        for order in self.order_id:
            available = order._qing_rental_available()
            requested = defaultdict(float)
            for item in order.qing_rental_line_ids:
                requested[item.product_id.id] += max(0, item.quantity - item.qty_returned)
            valid_period = (order.state != 'cancel' and order.qing_rental_start and order.qing_rental_end
                            and order.qing_rental_end > order.qing_rental_start)
            for line in self.filtered(lambda item: item.order_id == order):
                line.available_qty = available.get(line.product_id.id, 0)
                line.required_qty = requested[line.product_id.id]
                if valid_period and line.product_id and line.quantity > 0:
                    sufficient = line.product_id.uom_id.compare(line.required_qty, line.available_qty) <= 0
                    line.availability_state = 'available' if sufficient else 'unavailable'

    @api.constrains('product_id', 'service_product_id', 'quantity', 'rate', 'padding_hours', 'order_id')
    def _check_configuration(self):
        unit = self.env.ref('uom.product_uom_unit')
        for line in self:
            product, service = line.product_id, line.service_product_id
            if not line.order_id.is_qing_rental:
                raise ValidationError(self.env._('Equipment lines require a rental order.'))
            if not product.active or not product.qing_rental_ok or not product.is_storable or product.uom_id != unit:
                raise ValidationError(self.env._('Select rentable, tracked goods measured in units.'))
            if not service.active or service.type != 'service' or service.invoice_policy != 'order' or service.uom_id != unit:
                raise ValidationError(self.env._('Rental billing requires a service invoiced on ordered quantities, measured in units.'))
            for value in (line.quantity, line.rate, line.padding_hours):
                finite_nonnegative(self.env, value)
            if line.quantity <= 0 or abs(line.quantity - round(line.quantity)) > 1e-8:
                raise ValidationError(self.env._('Enter a positive whole number of rental units.'))

    def _occupancy_intervals(self, now):
        """Return quantities occupying capacity, including late returns and cleaning time."""
        self.ensure_one()
        start = self.order_id.qing_rental_start
        end = self.order_id.qing_rental_end + timedelta(hours=self.padding_hours)
        remaining = self.quantity - self.qty_returned
        result = [(start, end, remaining)] if remaining > 0 else []
        if self.qty_out > self.qty_returned and self.order_id.qing_rental_end < now:
            result.append((end, datetime.max, self.qty_out - self.qty_returned))
        # Early returns release only after the agreed preparation time has elapsed.
        returned = self.move_ids.sudo().filtered(lambda move: move.state == 'done' and move.qing_rental_direction == 'in')
        for move in returned:
            release = move.date + timedelta(hours=self.padding_hours)
            if release > now:
                result.append((start, release, move.quantity))
        return result

    @api.onchange('product_id')
    def _onchange_product(self):
        for line in self:
            if not line.product_id:
                continue
            product, order = line.product_id, line.order_id
            line.service_product_id = product.qing_rental_service_id
            line.period = product.qing_rental_period
            line.padding_hours = product.qing_rental_padding_hours
            line.rate = order.company_id.currency_id._convert(product.qing_rental_rate, order.currency_id, order.company_id, fields.Date.context_today(line))

    amount = fields.Monetary(compute='_compute_amount', compute_sudo=False, currency_field='currency_id')
    availability_state = fields.Selection([('available', 'Available'), ('unavailable', 'Unavailable')],
                                         compute='_compute_available', string='Rental availability')
    available_qty = fields.Float(compute='_compute_available', string='Available during period')
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    move_ids = fields.One2many('stock.move', 'qing_rental_line_id', readonly=True)
    order_id = fields.Many2one('sale.order', required=True, index=True, ondelete='cascade', check_company=True)
    padding_hours = fields.Float(string='Preparation hours', default=0)
    period = fields.Selection(PERIODS, required=True, default='day')
    product_id = fields.Many2one('product.product', required=True, check_company=True, ondelete='restrict', domain=[('qing_rental_ok', '=', True)])
    qty_out = fields.Float(compute='_compute_quantities', string='Picked up')
    qty_returned = fields.Float(compute='_compute_quantities', string='Returned')
    quantity = fields.Float(required=True, default=1, digits='Product Unit')
    rate = fields.Monetary(required=True, currency_field='currency_id', string='Rate per period')
    required_qty = fields.Float(compute='_compute_available', string='Required during period')
    sales_line_ids = fields.One2many('sale.order.line', 'qing_rental_line_id', readonly=True, copy=False)
    sequence = fields.Integer(default=10)
    service_product_id = fields.Many2one('product.product', required=True, check_company=True, domain=[('type', '=', 'service')])

    @api.model_create_multi
    def create(self, vals_list):
        check_operator(self.env)
        for vals in vals_list:
            if not is_internal(self.env) and set(vals) - EDITABLE_FIELDS:
                raise UserError(self.env._('Only rental equipment terms may be entered manually.'))
            if not is_internal(self.env) and any(key.startswith('default_') and key[8:] in self._fields and key[8:] not in EDITABLE_FIELDS for key in self.env.context):
                raise UserError(self.env._('Calculated rental fields cannot be supplied as defaults.'))
            for field in ('order_id', 'product_id'):
                vals.setdefault(field, self.env.context.get('default_' + field))
            order = self.env['sale.order'].browse(vals.get('order_id'))
            order.check_access('write')
            if order.state not in ('draft', 'sent') and not is_internal(self.env):
                raise UserError(self.env._('Add equipment before confirming the rental.'))
            product = self.env['product.product'].browse(vals.get('product_id'))
            product.check_access('read')
            vals.setdefault('service_product_id', product.qing_rental_service_id.id)
            vals.setdefault('period', product.qing_rental_period)
            vals.setdefault('padding_hours', product.qing_rental_padding_hours)
            vals.setdefault('rate', order.company_id.currency_id._convert(product.qing_rental_rate, order.currency_id, order.company_id, fields.Date.context_today(order)))
        return super().create(vals_list)

    def write(self, vals):
        self._check_editable()
        if not is_internal(self.env) and set(vals) - EDITABLE_FIELDS:
            raise UserError(self.env._('Rental quantities and accounting totals are calculated by the workflow.'))
        if 'order_id' in vals and any(line.order_id.id != vals['order_id'] for line in self):
            raise UserError(self.env._('Equipment cannot be moved to another order.'))
        return super().write(vals)

    def unlink(self):
        self._check_editable()
        linked = self.env['sale.order.line'].search([('qing_rental_line_id', 'in', self.ids)])
        from .common import internal
        internal(linked).unlink()
        return super().unlink()
