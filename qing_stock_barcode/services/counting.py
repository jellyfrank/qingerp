from odoo.exceptions import UserError

from .common import MAX_LINES, digest, internal, lock_records


def quants_for(session):
    domain = [('location_id', '=', session.inventory_location_id.id), ('company_id', '=', session.company_id.id)]
    return session.env['stock.quant'].search(domain, order='id')


def snapshot(session):
    return digest([[q.id, q.product_id.id, q.location_id.id, q.lot_id.id, q.owner_id.id,
                    q.package_id.id, q.quantity, q.reserved_quantity, q.write_date]
                   for q in quants_for(session)])


def prepare(session):
    quants = quants_for(session)
    if len(quants) > MAX_LINES:
        raise UserError(session.env._('Split this location into smaller counting areas before scanning.'))
    # Native quants with the same dimensions can temporarily coexist; expose one count per dimension.
    groups = {}
    for quant in quants:
        key = (quant.product_id.id, quant.lot_id.id, quant.package_id.id, quant.owner_id.id)
        if key in groups:
            raise UserError(session.env._('Duplicate inventory dimensions exist. Consolidate native quants before starting this count.'))
        groups[key] = {
            'session_id': session.id, 'quant_id': quant.id, 'product_id': quant.product_id.id,
            'uom_id': quant.product_id.uom_id.id, 'location_id': quant.location_id.id,
            'lot_id': quant.lot_id.id, 'owner_id': quant.owner_id.id, 'package_id': quant.package_id.id,
            'baseline_quantity': quant.quantity,
        }
    internal(session.env['qing.stock.scan.line']).create(list(groups.values()))
    internal(session).write({'snapshot_hash': snapshot(session)})


def apply(session):
    lines = session.line_ids.filtered('counted')
    if not lines:
        raise UserError(session.env._('Count at least one item. Uncounted items are not treated as zero.'))
    lock_records(session.inventory_location_id)
    lock_records(quants_for(session))
    if snapshot(session) != session.snapshot_hash:
        raise UserError(session.env._('Stock changed while counting. Cancel this count and recount the location.'))
    Quant = session.env['stock.quant'].with_context(inventory_mode=True)
    applied = Quant.browse()
    for line in lines:
        quant = line.quant_id
        if not quant:
            if line.product_id.tracking in ('lot', 'serial') and not line.lot_id:
                raise UserError(session.env._('Use an existing lot or serial number when counting tracked products.'))
            quant = Quant.create({
                'product_id': line.product_id.id, 'location_id': line.location_id.id,
                'lot_id': line.lot_id.id, 'owner_id': line.owner_id.id,
                'package_id': line.package_id.id, 'inventory_quantity': line.quantity,
            })
            internal(line).write({'quant_id': quant.id})
        else:
            quant.with_context(inventory_mode=True).write({'inventory_quantity': line.quantity})
        applied |= quant
    action = applied.action_apply_inventory()
    if isinstance(action, dict):
        # The entire application is one transaction. Do not leave partially staged quant counts.
        raise UserError(session.env._('The native inventory check requires review. Reopen the count in Inventory.'))
    internal(session).write({'state': 'done', 'active_key': False})
    return False
