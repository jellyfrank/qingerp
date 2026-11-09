from collections import defaultdict
from math import isfinite

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import internal, is_internal


RENTAL_DIRECTIONS = [('out', 'Rental Pickup'), ('in', 'Rental Return')]
RENTAL_LINK_FIELDS = frozenset({'qing_rental_line_id', 'qing_rental_direction'})
RENTAL_IDENTITY_FIELDS = RENTAL_LINK_FIELDS | {
    'product_id', 'uom_id', 'company_id', 'location_id', 'location_dest_id', 'picking_id',
}
RENTAL_DONE_FIELDS = RENTAL_IDENTITY_FIELDS | {
    'quantity', 'product_uom_qty', 'move_line_ids', 'state', 'picked',
}
EPSILON = 0.000001


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        self._check_qing_rental_unbound_transfers()
        rental_moves = self.filtered(lambda move: move.qing_rental_line_id and move.state not in ('done', 'cancel'))
        if not rental_moves:
            return super()._action_done(cancel_backorder=cancel_backorder)
        rental_moves.product_id._lock_rental_inventory()
        rental_moves._check_qing_rental_structure()
        result = super(StockMove, internal(self))._action_done(cancel_backorder=cancel_backorder)
        completed = result.filtered('qing_rental_line_id')
        completed._check_qing_rental_history()
        completed._check_qing_rental_physical_stock()
        # Never return an internal capability in an externally accessible recordset.
        return result.with_env(self.env)

    def _check_qing_rental_unbound_transfers(self):
        moves = self.filtered(lambda move: not move.qing_rental_line_id
                              and move.state not in ('done', 'cancel'))
        if not moves:
            return
        Warehouse = self.env['stock.warehouse'].sudo()
        domain = [('company_id', 'in', moves.company_id.ids), ('qing_rental_out_location_id', '!=', False)]
        locations = Warehouse.search(domain).qing_rental_out_location_id
        for move in moves:
            if any(move.location_id._child_of(location) or move.location_dest_id._child_of(location) for location in locations):
                # is_inventory is a writable native field, not a capability.
                # Keep genuine inventory corrections under native permissions,
                # but do not let a transfer opt out by forging this Boolean.
                inventory_sides = sum(location.usage == 'inventory' for location in (move.location_id, move.location_dest_id))
                if (move.is_inventory and not move.picking_id and inventory_sides == 1
                        and all(detail.location_id == move.location_id and detail.location_dest_id == move.location_dest_id
                                for detail in move.move_line_ids)):
                    continue
                raise ValidationError(_('Use rental order transfers for equipment entering or leaving On Rent locations.'))

    def _check_qing_rental_structure(self):
        """Read order configuration privately; inventory operators need no sale access."""
        unit = self.env.ref('uom.product_uom_unit')
        for move in self.sudo():
            line = move.qing_rental_line_id
            order = line.order_id
            warehouse = order.warehouse_id
            warehouse._check_qing_rental_locations()
            if order.state != 'sale' or not order.is_qing_rental:
                raise ValidationError(_('Only confirmed rental orders can move rental equipment.'))
            if not warehouse.qing_rental_stock_location_id or not warehouse.qing_rental_out_location_id:
                raise ValidationError(_('Configure the warehouse rental locations first.'))
            if move.product_id != line.product_id or not move.product_id.is_storable or move.uom_id != unit:
                raise ValidationError(_('Rental transfers must use the contracted equipment in Units.'))
            if move.company_id != order.company_id or move.picking_id.company_id != order.company_id:
                raise ValidationError(_('The rental order and transfer must belong to the same company.'))
            source = warehouse.qing_rental_stock_location_id
            destination = warehouse.qing_rental_out_location_id
            if move.qing_rental_direction == 'in':
                source, destination = destination, source
            if move.qing_rental_direction not in ('out', 'in'):
                raise ValidationError(_('A rental transfer must have a pickup or return direction.'))
            if move.qing_rental_direction == 'out' and not order.qing_rental_start <= fields.Datetime.now() < order.qing_rental_end:
                raise ValidationError(_('Rental pickup must be validated during the agreed rental period.'))
            if move.location_id != source or move.location_dest_id != destination:
                raise ValidationError(_('Use the rental stock locations configured on the order warehouse.'))
            if move.restrict_partner_id or move.picking_id.owner_id:
                raise ValidationError(_('Third-party owned equipment cannot be used for rentals.'))
            for detail in move.move_line_ids.filtered(lambda detail: detail.picked and detail.quantity):
                if (detail.product_id != move.product_id or detail.uom_id != unit
                        or detail.company_id != order.company_id or detail.picking_id != move.picking_id):
                    raise ValidationError(_('Rental operations must keep the equipment, unit and company of the transfer.'))
                if detail.location_id != source or detail.location_dest_id != destination or detail.owner_id:
                    raise ValidationError(_('Rental operation locations and ownership must match the transfer.'))
                if not isfinite(detail.quantity) or detail.quantity < 0 or abs(detail.quantity - round(detail.quantity)) > EPSILON:
                    raise ValidationError(_('Rental equipment quantities must be non-negative whole units.'))
                if move.product_id.tracking in ('lot', 'serial') and not detail.lot_id:
                    raise ValidationError(_('Select an existing lot or serial number for rental equipment.'))

    def _check_qing_rental_history(self):
        """Validate final native done quantities, including split/backorder movements."""
        if not self:
            return
        self._check_qing_rental_structure()
        Move = self.env['stock.move'].sudo()
        lines = self.sudo().qing_rental_line_id
        domain = [('qing_rental_line_id', 'in', lines.ids), ('state', '=', 'done')]
        history = Move.search(domain)
        totals = defaultdict(float)
        lot_balances = defaultdict(float)
        for move in history:
            direction = move.qing_rental_direction
            totals[(move.qing_rental_line_id.id, direction)] += move.quantity
            sign = 1 if direction == 'out' else -1
            for detail in move.move_line_ids:
                lot_balances[(move.qing_rental_line_id.id, detail.lot_id.id, detail.owner_id.id)] += sign * detail.quantity
        for line in lines:
            outgoing = totals[(line.id, 'out')]
            incoming = totals[(line.id, 'in')]
            if outgoing > line.quantity + EPSILON:
                raise ValidationError(_('The picked-up quantity exceeds the contracted rental quantity.'))
            if incoming > outgoing + EPSILON:
                raise ValidationError(_('The returned quantity exceeds the quantity actually picked up.'))
        if any(quantity < -EPSILON for quantity in lot_balances.values()):
            raise ValidationError(_('Return only the lots or serial numbers picked up on this rental order.'))
        serial_lots = self.filtered(lambda move: move.product_id.tracking == 'serial').move_line_ids.lot_id
        if not serial_lots:
            return
        domain = [('qing_rental_line_id', '!=', False), ('state', '=', 'done'),
                  ('company_id', 'in', self.company_id.ids), ('move_line_ids.lot_id', 'in', serial_lots.ids)]
        serial_balances = defaultdict(float)
        for move in Move.search(domain):
            sign = 1 if move.qing_rental_direction == 'out' else -1
            for detail in move.move_line_ids.filtered(lambda detail: detail.lot_id in serial_lots):
                serial_balances[detail.lot_id.id] += sign * detail.quantity
        if any(quantity > 1 + EPSILON for quantity in serial_balances.values()):
            raise ValidationError(_('This serial number is still rented on another order.'))

    def _check_qing_rental_physical_stock(self):
        if not self:
            return
        Quant = self.env['stock.quant'].sudo()
        domain = [('product_id', 'in', self.product_id.ids), ('location_id', 'in', self.location_id.ids),
                  ('company_id', 'in', self.company_id.ids), ('quantity', '<', -EPSILON)]
        if Quant.search_count(domain, limit=1):
            raise ValidationError(_('There is not enough physical equipment in the rental source location.'))

    @api.model
    def _prepare_merge_moves_distinct_fields(self):
        return super()._prepare_merge_moves_distinct_fields() + sorted(RENTAL_LINK_FIELDS)

    qing_rental_direction = fields.Selection(RENTAL_DIRECTIONS, string='Rental Operation', copy=True, readonly=True)
    qing_rental_line_id = fields.Many2one(
        'qing.rental.line', string='Rental Equipment Line', ondelete='restrict', copy=True, index=True, readonly=True,
    )

    def copy_data(self, default=None):
        if not is_internal(self.env) and self.filtered('qing_rental_line_id'):
            raise UserError(_('Create rental transfers from the rental order; they cannot be duplicated.'))
        return super().copy_data(default=default)

    @api.model_create_multi
    def create(self, vals_list):
        if not is_internal(self.env):
            if any(any(vals.get(field) for field in RENTAL_LINK_FIELDS) for vals in vals_list):
                raise AccessError(_('Rental transfers can only be created by the rental order workflow.'))
            picking_ids = {vals['picking_id'] for vals in vals_list if vals.get('picking_id')}
            domain = [('picking_id', 'in', list(picking_ids)), ('qing_rental_line_id', '!=', False)]
            if picking_ids and self.sudo().search_count(domain, limit=1):
                raise AccessError(_('Additional equipment must be added through the rental order.'))
        moves = super().create(vals_list)
        # Context defaults are part of RPC input too; inspect the created values.
        if not is_internal(self.env) and moves.filtered(lambda move: move.qing_rental_line_id or move.qing_rental_direction):
            raise AccessError(_('Rental transfers can only be created by the rental order workflow.'))
        return moves

    def write(self, vals):
        rentals = self.filtered('qing_rental_line_id')
        if not is_internal(self.env):
            if RENTAL_LINK_FIELDS.intersection(vals):
                raise AccessError(_('Rental transfer links cannot be changed manually.'))
            if rentals and RENTAL_IDENTITY_FIELDS.intersection(vals):
                for move in rentals:
                    if any(move[name].id != vals[name] for name in RENTAL_IDENTITY_FIELDS.intersection(vals)):
                        raise AccessError(_('Rental equipment and locations cannot be changed manually.'))
            if rentals.filtered(lambda move: move.state == 'done') and RENTAL_DONE_FIELDS.intersection(vals):
                raise AccessError(_('Completed rental transfers cannot be edited; use the rental return workflow.'))
            if rentals and vals.get('state') == 'done':
                raise AccessError(_('Validate rental transfers using the normal transfer validation action.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda move: move.qing_rental_line_id and move.state == 'done'):
            raise AccessError(_('Completed rental transfers cannot be deleted.'))
        return super().unlink()
