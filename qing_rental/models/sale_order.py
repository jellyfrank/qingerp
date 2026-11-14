from collections import defaultdict
from datetime import timedelta

from odoo import api, Command, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_datetime

from .common import check_operator, internal, is_internal, periods


RENTAL_STATES = [('draft', 'Quotation'), ('booked', 'Booked'), ('out', 'Picked up'),
                 ('partial', 'Partially picked up / returned'), ('returned', 'Returned'), ('cancel', 'Cancelled')]
CALCULATED_FIELDS = {'qing_rental_status', 'qing_rental_deposit_balance', 'qing_rental_picking_count'}


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _qing_rental_lock(self):
        check_operator(self.env)
        self.check_access('write')
        self.flush_recordset()
        for order_id in sorted(self.ids):
            self.env.cr.execute('UPDATE sale_order SET id = id WHERE id = %s', [order_id])
        self.invalidate_recordset()

    def _qing_rental_validate(self):
        for order in self:
            if not order.is_qing_rental or not order.qing_rental_line_ids:
                raise UserError(self.env._('Add at least one piece of equipment to a rental order.'))
            if not order.qing_rental_start or not order.qing_rental_end or order.qing_rental_end <= order.qing_rental_start:
                raise ValidationError(self.env._('The rental end must be after its start.'))
            warehouse = order.warehouse_id
            if not warehouse.qing_rental_stock_location_id or not warehouse.qing_rental_out_location_id:
                raise UserError(self.env._('Configure the warehouse rental locations first.'))
            order.qing_rental_line_ids._check_configuration()

    def _qing_rental_available(self, end=None):
        """Peak concurrent occupancy, using company-owned units in the dedicated pool."""
        self.ensure_one()
        if not self.qing_rental_start or not (end or self.qing_rental_end):
            return {}
        lines = self.qing_rental_line_ids
        products = lines.product_id
        warehouse = self.warehouse_id
        if not products or not warehouse.qing_rental_stock_location_id or not warehouse.qing_rental_out_location_id:
            return {}
        locations = warehouse.qing_rental_stock_location_id | warehouse.qing_rental_out_location_id
        Quant = self.env['stock.quant'].sudo()
        domain = [('product_id', 'in', products.ids), ('company_id', '=', self.company_id.id),
                  ('location_id', 'child_of', locations.ids), ('owner_id', '=', False)]
        capacity = {product.id: qty for product, qty in Quant._read_group(domain, ['product_id'], ['quantity:sum'])}
        Line = self.env['qing.rental.line'].sudo()
        domain = [('company_id', '=', self.company_id.id), ('order_id.state', '=', 'sale'),
                  ('order_id.warehouse_id', '=', warehouse.id), ('order_id', '!=', self._origin.id),
                  ('product_id', 'in', products.ids)]
        # Capacity checks must count bookings hidden by the salesperson's own-order rules.
        # Return quantities only, never the other customer's identity or prices.
        peers = Line.search(domain)
        now = fields.Datetime.now()
        available = {}
        for product in products:
            own = lines.filtered(lambda line: line.product_id == product)
            start = self.qing_rental_start
            finish = (end or self.qing_rental_end) + timedelta(hours=max(own.mapped('padding_hours'), default=0))
            events = []
            for peer in peers.filtered(lambda line: line.product_id == product):
                for begin, stop, qty in peer._occupancy_intervals(now):
                    if begin < finish and stop > start:
                        events.extend([(max(start, begin), qty), (min(finish, stop), -qty)])
            # Returns at a boundary release capacity before pickups at the same instant.
            current = peak = 0
            for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
                current += delta
                peak = max(peak, current)
            available[product.id] = max(0, capacity.get(product.id, 0) - peak)
        return available

    def _qing_rental_check_capacity(self, end=None):
        for order in self:
            available = order._qing_rental_available(end=end)
            requested = defaultdict(float)
            for line in order.qing_rental_line_ids:
                requested[line.product_id.id] += max(0, line.quantity - line.qty_returned)
            for product in order.qing_rental_line_ids.product_id:
                if product.uom_id.compare(requested[product.id], available.get(product.id, 0)) > 0:
                    raise UserError(self.env._('Not enough rental capacity for %(product)s during this period (available: %(qty)s).',
                                              product=product.display_name, qty=available.get(product.id, 0)))

    def _qing_rental_charge_values(self, line):
        self.ensure_one()
        price = line.rate * periods(self.qing_rental_start, self.qing_rental_end, line.period)
        return {'product_id': line.service_product_id.id, 'product_uom_id': line.service_product_id.uom_id.id,
                'product_uom_qty': line.quantity, 'price_unit': price,
                'qing_rental_billed_start': self.qing_rental_start, 'qing_rental_billed_end': self.qing_rental_end,
                'name': self.env._('%(equipment)s — Rental: %(start)s → %(end)s', equipment=line.product_id.display_name,
                                   start=format_datetime(self.env, self.qing_rental_start), end=format_datetime(self.env, self.qing_rental_end))}

    def _qing_rental_check_quote_billing(self):
        for order in self.filtered(lambda item: item.is_qing_rental and item.state in ('draft', 'sent')):
            order._qing_rental_validate()
            for line in order.qing_rental_line_ids:
                expected = order._qing_rental_charge_values(line)
                charge = order.order_line.filtered(lambda row: row.qing_rental_line_id == line and row.qing_rental_charge == 'rent')
                if (len(charge) != 1 or charge.product_id != line.service_product_id or charge.product_uom_qty != line.quantity
                        or abs(charge.price_unit - expected['price_unit']) > 1e-8
                        or charge.qing_rental_billed_start != order.qing_rental_start or charge.qing_rental_billed_end != order.qing_rental_end):
                    raise UserError(self.env._('Update rental charges before printing or sharing this quotation.'))

    def _qing_rental_sync_prices(self):
        SaleLine = internal(self.env['sale.order.line'])
        for order in self:
            for line in order.qing_rental_line_ids:
                values = order._qing_rental_charge_values(line)
                existing = order.order_line.filtered(lambda row: row.qing_rental_line_id == line and row.qing_rental_charge == 'rent')
                if existing:
                    internal(existing).write(values)
                else:
                    SaleLine.create(dict(values, order_id=order.id, qing_rental_line_id=line.id, qing_rental_charge='rent'))

    def _get_copiable_order_lines(self):
        return super()._get_copiable_order_lines().filtered(lambda line: not line.qing_rental_line_id)

    @api.depends('qing_rental_line_ids.move_ids.state', 'qing_rental_line_ids.move_ids.quantity',
                 'qing_rental_line_ids.move_ids.qing_rental_direction', 'qing_rental_line_ids.quantity', 'state')
    def _compute_qing_rental_status(self):
        for order in self:
            status = 'draft'
            if order.state == 'cancel':
                status = 'cancel'
            elif order.state == 'sale':
                lines = order.qing_rental_line_ids
                out, returned, planned = sum(lines.mapped('qty_out')), sum(lines.mapped('qty_returned')), sum(lines.mapped('quantity'))
                status = 'booked'
                if out:
                    status = 'returned' if returned >= planned else 'partial' if returned or out < planned else 'out'
            order.qing_rental_status = status

    def _compute_qing_rental_picking_count(self):
        for order in self:
            order.qing_rental_picking_count = len(order.qing_rental_line_ids.move_ids.picking_id)

    @api.depends('qing_rental_deposit_ids.amount', 'qing_rental_deposit_ids.kind', 'qing_rental_deposit_ids.state', 'qing_rental_deposit_ids.move_id.state')
    def _compute_qing_rental_deposit_balance(self):
        for order in self:
            posted = order.qing_rental_deposit_ids.filtered(lambda item: item.state == 'posted' and item.move_id.state == 'posted')
            order.qing_rental_deposit_balance = sum(item.amount * (1 if item.kind == 'receipt' else -1) for item in posted)

    @api.constrains('is_qing_rental', 'qing_rental_start', 'qing_rental_end')
    def _check_qing_rental_dates(self):
        for order in self.filtered('is_qing_rental'):
            if not order.qing_rental_start or not order.qing_rental_end or order.qing_rental_end <= order.qing_rental_start:
                raise ValidationError(self.env._('The rental end must be after its start.'))

    def _qing_rental_transfer(self, direction):
        self.ensure_one()
        self._qing_rental_lock()
        if self.state != 'sale' or not self.is_qing_rental:
            raise UserError(self.env._('Confirm the rental before creating transfers.'))
        self.env['stock.picking'].check_access('create')
        lines = self.qing_rental_line_ids
        lines.product_id._lock_rental_inventory()
        if direction == 'out':
            now = fields.Datetime.now()
            if now < self.qing_rental_start or now >= self.qing_rental_end:
                raise UserError(self.env._('Pickup must take place during the booked period. Adjust or renew the rental first.'))
            self._qing_rental_check_capacity()
        pending = lines.move_ids.filtered(lambda move: move.state not in ('done', 'cancel') and move.qing_rental_direction == direction).picking_id
        if pending:
            return self._qing_rental_picking_action(pending)
        warehouse = self.warehouse_id
        source, destination = warehouse.qing_rental_stock_location_id, warehouse.qing_rental_out_location_id
        if direction == 'in':
            source, destination = destination, source
        commands = []
        for line in lines:
            qty = line.quantity - line.qty_out if direction == 'out' else line.qty_out - line.qty_returned
            if qty <= 0:
                continue
            commands.append(Command.create({'product_id': line.product_id.id, 'uom_id': line.product_id.uom_id.id,
                'product_uom_qty': qty, 'location_id': source.id, 'location_dest_id': destination.id,
                'company_id': self.company_id.id, 'qing_rental_line_id': line.id, 'qing_rental_direction': direction}))
        if not commands:
            raise UserError(self.env._('There are no remaining units to pick up or return.'))
        picking = internal(self.env['stock.picking']).create({'partner_id': self.partner_shipping_id.id,
            'company_id': self.company_id.id, 'picking_type_id': warehouse.int_type_id.id,
            'location_id': source.id, 'location_dest_id': destination.id, 'origin': self.name,
            'scheduled_date': fields.Datetime.now(), 'move_ids': commands})
        picking.action_confirm()
        picking.action_assign()
        return self._qing_rental_picking_action(picking)

    def _qing_rental_picking_action(self, pickings):
        pickings.check_access('read')
        result = {'type': 'ir.actions.act_window', 'name': self.env._('Rental transfers'), 'res_model': 'stock.picking',
                  'views': [(False, 'list'), (False, 'form')], 'domain': [('id', 'in', pickings.ids)]}
        if len(pickings) == 1:
            result.update(views=[(False, 'form')], res_id=pickings.id)
        return result

    def _action_cancel(self):
        rentals = self.filtered('is_qing_rental')
        if rentals:
            rentals._qing_rental_lock()
            rentals.qing_rental_line_ids.product_id._lock_rental_inventory()
            if any(line.qty_out for line in rentals.qing_rental_line_ids):
                raise UserError(self.env._('A picked-up rental cannot be cancelled. Complete its return and settlement.'))
            if any(order.sudo().qing_rental_deposit_balance for order in rentals):
                raise UserError(self.env._('Refund the outstanding deposit before cancelling.'))
            pending = rentals.qing_rental_line_ids.move_ids.filtered(lambda move: move.state not in ('done', 'cancel'))
            pending._action_cancel()
        return super(SaleOrder, internal(self))._action_cancel()

    is_qing_rental = fields.Boolean(string='Rental order', copy=True)
    qing_rental_deposit_balance = fields.Monetary(compute='_compute_qing_rental_deposit_balance', groups='qing_rental.group_rental_manager')
    qing_rental_deposit_ids = fields.One2many('qing.rental.deposit', 'order_id', groups='qing_rental.group_rental_manager', copy=False)
    qing_rental_end = fields.Datetime(string='Rental end', default=lambda self: fields.Datetime.now() + timedelta(days=1), copy=True)
    qing_rental_line_ids = fields.One2many('qing.rental.line', 'order_id', string='Rental equipment', copy=True)
    qing_rental_picking_count = fields.Integer(compute='_compute_qing_rental_picking_count')
    qing_rental_start = fields.Datetime(string='Rental start', default=fields.Datetime.now, copy=True)
    qing_rental_status = fields.Selection(RENTAL_STATES, compute='_compute_qing_rental_status', store=True, string='Rental status')

    @api.model_create_multi
    def create(self, vals_list):
        if not is_internal(self.env) and (any(CALCULATED_FIELDS.intersection(vals) for vals in vals_list) or any('default_' + name in self.env.context for name in CALCULATED_FIELDS)):
            raise UserError(self.env._('Rental status and totals are calculated by the workflow.'))
        for vals in vals_list:
            if vals.get('is_qing_rental', self.env.context.get('default_is_qing_rental')):
                check_operator(self.env)
                if vals.get('state', self.env.context.get('default_state', 'draft')) not in ('draft', 'sent'):
                    raise UserError(self.env._('Create a rental quotation before confirming it.'))
        return super().create(vals_list)

    def write(self, vals):
        if not is_internal(self.env) and CALCULATED_FIELDS.intersection(vals):
            raise UserError(self.env._('Rental status and totals are calculated by the workflow.'))
        rentals = self.filtered('is_qing_rental')
        if rentals or vals.get('is_qing_rental'):
            if not is_internal(self.env):
                protected = {'qing_rental_start', 'qing_rental_end', 'company_id', 'warehouse_id', 'currency_id', 'partner_id', 'partner_shipping_id', 'is_qing_rental'}
                if (protected | {'qing_rental_line_ids'}).intersection(vals):
                    check_operator(self.env)
                if protected.intersection(vals) and any(order.state not in ('draft', 'sent') for order in rentals):
                    raise UserError(self.env._('Confirmed rental terms are locked. Use the renewal action.'))
                if 'state' in vals and (vals['state'] != 'sent' or any(order.state not in ('draft', 'sent') for order in rentals)):
                    raise UserError(self.env._('Use the rental workflow actions to change state.'))
                if vals.get('is_qing_rental') and any(order.state not in ('draft', 'sent') for order in self):
                    raise UserError(self.env._('Only quotations can be converted into rentals.'))
                if vals.get('is_qing_rental') is False and rentals.qing_rental_line_ids:
                    raise UserError(self.env._('Remove rental equipment before converting this quotation.'))
        return super().write(vals)

    def action_confirm(self):
        rentals = self.filtered('is_qing_rental')
        if rentals:
            rentals._qing_rental_lock()
            if any(order.state not in ('draft', 'sent') for order in rentals):
                raise UserError(self.env._('This rental is already confirmed or cancelled.'))
            rentals._qing_rental_validate()
            rentals.qing_rental_line_ids.product_id._lock_rental_inventory()
            # Sequential confirmation makes each order visible to the next capacity check.
            for order in rentals:
                order._qing_rental_check_capacity()
                order._qing_rental_sync_prices()
                super(SaleOrder, internal(order)).action_confirm()
        ordinary = self - rentals
        return super(SaleOrder, ordinary).action_confirm() if ordinary else True

    def action_draft(self):
        rentals = self.filtered('is_qing_rental')
        if rentals:
            rentals._qing_rental_lock()
            if any(order.state != 'cancel' for order in rentals):
                raise UserError(self.env._('Only cancelled rentals may be reset to a quotation.'))
        return super(SaleOrder, internal(self)).action_draft()

    def action_qing_rental_reprice(self):
        self._qing_rental_lock()
        if any(order.state not in ('draft', 'sent') for order in self):
            raise UserError(self.env._('Only quotations can be repriced.'))
        self._qing_rental_validate()
        self._qing_rental_sync_prices()
        return True

    def action_quotation_send(self):
        rentals = self.filtered(lambda order: order.is_qing_rental and order.state in ('draft', 'sent'))
        if rentals:
            rentals.action_qing_rental_reprice()
        return super().action_quotation_send()

    def action_preview_sale_order(self):
        rentals = self.filtered(lambda order: order.is_qing_rental and order.state in ('draft', 'sent'))
        if rentals:
            rentals.action_qing_rental_reprice()
        return super().action_preview_sale_order()

    def action_qing_rental_availability(self):
        self.ensure_one()
        check_operator(self.env)
        self.check_access('read')
        self._qing_rental_validate()
        self._qing_rental_check_capacity()
        return self._qing_notify(title=self.env._('Rental availability'), message=self.env._('The requested equipment is available. Capacity is reserved only on confirmation.'),
            notification_type='success')

    def action_qing_rental_pickup(self):
        return self._qing_rental_transfer('out')

    def action_qing_rental_return(self):
        return self._qing_rental_transfer('in')

    def action_qing_rental_pickings(self):
        self.ensure_one()
        check_operator(self.env)
        self.check_access('read')
        return self._qing_rental_picking_action(self.qing_rental_line_ids.move_ids.picking_id)

    def action_qing_rental_renew(self):
        self.ensure_one()
        check_operator(self.env)
        self.check_access('write')
        if self.state != 'sale' or not self.is_qing_rental:
            raise UserError(self.env._('Only confirmed rentals can be renewed.'))
        return {'type': 'ir.actions.act_window', 'name': self.env._('Renew rental'), 'res_model': 'qing.rental.renew',
                'views': [(False, 'form')], 'target': 'new', 'context': {'default_order_id': self.id}}
