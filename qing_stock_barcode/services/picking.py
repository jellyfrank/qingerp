from collections import defaultdict

from odoo.exceptions import UserError

from .common import MAX_LINES, digest, internal, is_manager, lock_records


def snapshot(picking):
    # Stored recomputations update SQL write_date without refreshing its cached value.
    # Flush first, then reload timestamps so our own writes do not look like outside edits.
    records = (picking.move_line_ids, picking.move_ids, picking)
    for recordset in records:
        recordset.flush_recordset()
    for recordset in records:
        recordset.invalidate_recordset(['write_date'], flush=False)
    moves = picking.move_ids.filtered(lambda move: move.state not in ('done', 'cancel'))
    return digest({
        'picking': [picking.id, picking.state, picking.write_date, picking.company_id.id,
                    picking.location_id.id, picking.location_dest_id.id, picking.picking_type_id.id],
        'moves': [[m.id, m.write_date, m.product_id.id, m.product_uom_qty, m.uom_id.id,
                   m.state, m.picked, m.location_id.id, m.location_dest_id.id] for m in moves.sorted('id')],
        'lines': [[l.id, l.write_date, l.move_id.id, l.product_id.id, l.quantity, l.picked,
                   l.uom_id.id, l.location_id.id, l.location_dest_id.id, l.lot_id.id,
                   l.lot_name, l.owner_id.id, l.package_id.id, l.result_package_id.id]
                  for l in moves.move_line_ids.sorted('id')],
    })


def lock(picking):
    picking.check_access('write')
    lock_records(picking)
    lock_records(picking.move_ids)
    lock_records(picking.move_line_ids)


def check_current(session):
    picking = session.picking_id
    lock(picking)
    if picking.state in ('done', 'cancel', 'draft'):
        raise UserError(session.env._('The transfer is no longer available for scanning.'))
    if snapshot(picking) != session.snapshot_hash:
        raise UserError(session.env._('The transfer changed outside this session. Cancel and reopen it to reload current reservations.'))


def prepare(session):
    picking = session.picking_id
    if picking.state in ('draft', 'done', 'cancel'):
        raise UserError(session.env._('Confirm the transfer before starting a scan session.'))
    if any(picking.move_line_ids.mapped('picked')):
        raise UserError(session.env._('This transfer already contains manually picked lines. Review those quantities in Inventory before starting a scan session.'))
    moves = picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
    if len(moves) + len(moves.move_line_ids) > MAX_LINES:
        raise UserError(session.env._('Split this transfer into smaller operations before scanning.'))
    values = []
    for move in moves:
        for line in move.move_line_ids:
            values.append({
                'session_id': session.id, 'move_id': move.id, 'move_line_id': line.id,
                'product_id': line.product_id.id, 'uom_id': line.uom_id.id,
                'location_id': line.location_id.id, 'location_dest_id': line.location_dest_id.id,
                'lot_id': line.lot_id.id, 'lot_name': line.lot_name,
                'owner_id': line.owner_id.id, 'package_id': line.package_id.id,
                'result_package_id': line.result_package_id.id,
                'baseline_quantity': line.quantity,
                'planned_quantity': move.product_uom_qty,
            })
        if not move.move_line_ids:
            values.append({
                'session_id': session.id, 'move_id': move.id, 'product_id': move.product_id.id,
                'uom_id': move.uom_id.id, 'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
                'planned_quantity': move.product_uom_qty,
            })
    internal(session.env['qing.stock.scan.line']).create(values)
    internal(session).write({'snapshot_hash': snapshot(picking)})


def check_stock(session):
    lines = session.line_ids.filtered(lambda l: l.quantity > 0 and l.location_id.usage in ('internal', 'transit'))
    if not lines:
        return
    domain = [
        ('company_id', '=', session.company_id.id),
        ('product_id', 'in', lines.product_id.ids), ('location_id', 'in', lines.location_id.ids),
    ]
    quants = session.env['stock.quant'].search(domain)
    lock_records(quants)
    available = defaultdict(float)
    requested = defaultdict(float)
    for quant in quants:
        key = (quant.product_id.id, quant.location_id.id, quant.lot_id.id, quant.package_id.id, quant.owner_id.id)
        available[key] += quant.quantity
    for line in lines:
        key = (line.product_id.id, line.location_id.id, line.lot_id.id, line.package_id.id, line.owner_id.id)
        requested[key] += line.uom_id._compute_quantity(line.quantity, line.product_id.uom_id, round=False)
    for key, amount in requested.items():
        if amount > available[key] + 1e-8:
            raise UserError(session.env._('Available stock changed or is insufficient. Refresh the transfer before continuing.'))


def check_serial_receipts(session):
    lines = session.line_ids.filtered(lambda l: l.quantity > 0 and l.product_id.tracking == 'serial'
                                     and l.location_id.usage not in ('internal', 'transit'))
    if not lines:
        return
    # Serialize receipts of the same product before creating or checking serials.
    lock_records(lines.product_id, serialize=True)
    domain = [
        ('product_id', 'in', lines.product_id.ids), ('location_id.usage', 'in', ('internal', 'transit')),
        ('quantity', '>', 0), ('company_id', '=', session.company_id.id),
    ]
    quants = session.env['stock.quant'].search(domain)
    names = {(q.product_id.id, q.lot_id.name) for q in quants if q.lot_id}
    for line in lines:
        if (line.product_id.id, line.lot_id.name or line.lot_name) in names:
            raise UserError(session.env._('This serial number is already in stock.'))


def validation_blocker(session):
    """Read-only UI prerequisites; stock and concurrency checks still run under lock."""
    if session.state not in ('scanning', 'applied') or session.picking_id.state in ('draft', 'done', 'cancel'):
        return session.env._('The transfer is no longer available for scanning.')
    if session.user_id != session.env.user and not is_manager(session.env):
        return session.env._('Only the assigned operator or a scan manager can validate this session.')
    if session.pending_product_id:
        return session.env._('Complete or clear the pending lot or serial scan first.')
    lines = session.line_ids.filtered(lambda line: line.quantity > 0)
    if not lines:
        return session.env._('Scan at least one item before completing the transfer.')
    operation = session.picking_id.picking_type_id
    if operation.qing_scan_source_required and not session.source_confirmed:
        return session.env._('Scan the source location first.')
    if operation.qing_scan_destination_required and not session.destination_confirmed:
        return session.env._('Scan the destination location first.')
    if any(line.product_id.tracking in ('lot', 'serial') and not (line.lot_id or line.lot_name) for line in lines):
        return session.env._('A lot or serial number is required for every tracked item.')
    return False


def apply(session):
    check_current(session)
    blocker = validation_blocker(session)
    if blocker:
        raise UserError(blocker)
    lines = session.line_ids.filtered(lambda line: line.quantity > 0)
    check_stock(session)
    check_serial_receipts(session)
    for line in lines:
        values = {
            'quantity': line.quantity, 'picked': True,
            'lot_id': line.lot_id.id, 'lot_name': line.lot_name,
            'location_id': line.location_id.id, 'location_dest_id': line.location_dest_id.id,
            'package_id': line.package_id.id, 'result_package_id': line.result_package_id.id,
            'owner_id': line.owner_id.id,
        }
        if line.move_line_id:
            line.move_line_id.write(values)
        else:
            values.update({
                'move_id': line.move_id.id, 'picking_id': session.picking_id.id,
                'product_id': line.product_id.id, 'uom_id': line.uom_id.id,
                'company_id': session.company_id.id,
            })
            native_line = session.env['stock.move.line'].create(values)
            internal(line).write({'move_line_id': native_line.id})
    internal(session).write({'state': 'applied', 'snapshot_hash': snapshot(session.picking_id)})


def validate(session):
    if session.state == 'scanning':
        apply(session)
    check_current(session)
    check_stock(session)
    result = session.picking_id.button_validate()
    if session.picking_id.state == 'done':
        internal(session).write({'state': 'done', 'active_key': False})
    else:
        internal(session).write({'snapshot_hash': snapshot(session.picking_id)})
    return result if isinstance(result, dict) else False


def allow_native_validation(session):
    if session.state != 'applied':
        raise UserError(session.env._('Finish or cancel the active scan session before validating this transfer.'))
    if session.user_id != session.env.user and not is_manager(session.env):
        raise UserError(session.env._('Only the assigned operator or a scan manager can validate this session.'))
    check_current(session)
    check_stock(session)
    check_serial_receipts(session)
    # Backorder choices and operation-type policy are enforced by native button_validate.
