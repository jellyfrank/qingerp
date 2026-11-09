from collections import defaultdict

from odoo.exceptions import UserError

from .common import MAX_CODE_LENGTH, internal, quantity, scoped_record


def parse(env, code):
    if not isinstance(code, str) or not 1 <= len(code) <= MAX_CODE_LENGTH:
        raise UserError(env._('Scan a barcode between 1 and 512 characters.'))
    code = code.strip('\r\n\t ')
    nomenclature = env.company.nomenclature_id
    parsed = nomenclature.parse_barcode(code) if nomenclature else None
    result = {'code': code, 'amount': 1.0, 'lot': False, 'gs1': False, 'sku': code}
    if isinstance(parsed, list):
        result['gs1'] = True
        result['sku'] = False
        seen = set()
        for item in parsed:
            ai = item.get('ai', '')
            if ai not in ('00', '01', '02', '10', '21', '30', '37') and not ai.startswith('310'):
                raise UserError(env._('This GS1 application identifier is not supported: %s', ai))
            kind = item['type']
            bucket = 'lot' if ai in ('10', '21') else 'amount' if ai in ('30', '37') or ai.startswith('310') else 'code'
            if bucket in seen:
                raise UserError(env._('The GS1 barcode contains conflicting identifiers.'))
            seen.add(bucket)
            if bucket == 'lot':
                result['lot'] = str(item['value'])
            elif bucket == 'amount':
                result['amount'] = item['value']
                if ai.startswith('310'):
                    result['weight_uom'] = env.ref('uom.product_uom_kg')
            else:
                result['code'] = str(item['value'])
                result['kind'] = 'package' if kind == 'package' else 'product'
        if 'code' not in seen:
            raise UserError(env._('The GS1 barcode must identify a product or package.'))
    elif isinstance(parsed, dict) and parsed.get('type') != 'error':
        result['code'] = parsed.get('base_code') or code
        if parsed.get('type') == 'weight':
            result['sku'] = False
            result['amount'] = parsed['value']
    elif nomenclature.is_gs1_nomenclature and (code.startswith((']C1', ']d2', ']Q3')) or '\x1d' in code):
        raise UserError(env._('The GS1 barcode could not be parsed. Check the separator and label.'))
    return result


def matches(env, parsed, kind='auto', allow_empty=False):
    code = parsed['code']
    codes = [code]
    if parsed['gs1'] and len(code) == 14 and code.startswith('0'):
        codes.append(code[1:])
    result = []
    if kind in ('auto', 'product', 'lookup'):
        domain = [('company_id', 'in', (False, env.company.id))]
        if parsed.get('sku'):
            domain += ['|', ('barcode', 'in', codes), ('default_code', '=', parsed['sku'])]
        else:
            domain.append(('barcode', 'in', codes))
        products = env['product.product'].search(domain)
        result.extend(('product', p, p.uom_id) for p in products)
        pack_units = env['product.uom'].search([('barcode', 'in', codes), ('company_id', 'in', (False, env.company.id))])
        result.extend(('product', p.product_id, p.uom_id) for p in pack_units)
    if kind in ('auto', 'source', 'destination', 'lookup'):
        locations = env['stock.location'].search([('barcode', '=', code), ('company_id', '=', env.company.id)])
        result.extend(('location', loc, False) for loc in locations)
    if kind in ('auto', 'package', 'lookup'):
        packages = env['stock.package'].search([('name', '=', code), ('company_id', 'in', (False, env.company.id))])
        result.extend(('package', pack, False) for pack in packages)
    if kind == 'lookup':
        lots = env['stock.lot'].search([('name', '=', code), ('company_id', 'in', (False, env.company.id))])
        result.extend(('lot', lot, False) for lot in lots)
    if not result:
        if allow_empty:
            return False
        raise UserError(env._('Unknown barcode. Check the label or select the item manually.'))
    if len(result) != 1:
        raise UserError(env._('This barcode or SKU has multiple matches. Choose a scan mode or select the product manually.'))
    return result[0]


def location(session, record, kind):
    if session.kind == 'inventory':
        if record != session.inventory_location_id:
            raise UserError(session.env._('Scan the location assigned to this count.'))
        internal(session).write({'source_confirmed': True})
        return session.env._('Counting location confirmed.')
    picking = session.picking_id
    source_ok = record._child_of(picking.location_id)
    destination_ok = record._child_of(picking.location_dest_id)
    if kind == 'auto':
        if source_ok == destination_ok:
            raise UserError(session.env._('Choose Source or Destination before scanning this location.'))
        kind = 'source' if source_ok else 'destination'
    if kind == 'source' and source_ok:
        internal(session).write({'source_id': record.id, 'source_confirmed': True})
    elif kind == 'destination' and destination_ok:
        internal(session).write({'destination_id': record.id, 'destination_confirmed': True})
    else:
        raise UserError(session.env._('This location does not belong to the transfer route.'))
    return session.env._('Location confirmed: %s', record.display_name)


def _lot(session, product, name):
    if not name:
        return session.env['stock.lot']
    domain = [('product_id', '=', product.id), ('name', '=', name), ('company_id', 'in', (False, session.company_id.id))]
    lots = session.env['stock.lot'].search(domain)
    if len(lots) > 1:
        raise UserError(session.env._('This lot is ambiguous in the current company. Resolve its master data first.'))
    if not lots and (session.kind == 'inventory' or not session.picking_id.picking_type_id.use_create_lots
                     or session.picking_id.location_id.usage in ('internal', 'transit')):
        raise UserError(session.env._('Use an existing lot or serial number for this operation.'))
    if lots and session.kind == 'picking' and not session.picking_id.picking_type_id.use_existing_lots:
        raise UserError(session.env._('This operation does not allow existing lot numbers.'))
    return lots


def _candidates(session, product, lot, lot_name, selected_id):
    lines = session.line_ids.filtered(lambda l: l.product_id == product)
    if session.kind == 'picking':
        if session.source_id:
            lines = lines.filtered(lambda l: l.location_id == session.source_id)
        if session.destination_id:
            lines = lines.filtered(lambda l: l.location_dest_id == session.destination_id
                                   or l.location_id.usage not in ('internal', 'transit'))
    if selected_id:
        lines = lines.filtered(lambda l: l.id == selected_id)
        if not lines:
            raise UserError(session.env._('The selected line does not match this scan.'))
    if lot_name:
        exact = lines.filtered(lambda l: (l.lot_id == lot if lot else l.lot_name == lot_name))
        if exact:
            lines = exact
        else:
            lines = lines.filtered(lambda l: not l.lot_id and not l.lot_name)
    return lines


def add_product(session, product, amount, input_uom, lot_name=False, selected_id=False, absolute=False):
    if lot_name and (not isinstance(lot_name, str) or len(lot_name) > 100):
        raise UserError(session.env._('Scan a valid lot or serial number.'))
    if not product.is_storable:
        raise UserError(session.env._('Only tracked inventory products can be counted or moved here.'))
    if product.company_id and product.company_id != session.company_id:
        raise UserError(session.env._('The product belongs to another company.'))
    amount = quantity(session.env, amount, input_uom, allow_zero=absolute)
    if not input_uom._has_common_reference(product.uom_id):
        raise UserError(session.env._('The barcode unit is not compatible with the product unit.'))
    if session.kind == 'picking':
        if product not in session.picking_id.move_ids.product_id:
            raise UserError(session.env._('This product is not expected on this transfer.'))
        operation = session.picking_id.picking_type_id
        if operation.qing_scan_source_required and not session.source_confirmed:
            raise UserError(session.env._('Scan the source location first.'))
        if operation.qing_scan_destination_required and not session.destination_confirmed:
            raise UserError(session.env._('Scan the destination location first.'))
    if product.tracking in ('lot', 'serial') and not lot_name:
        internal(session).write({
            'pending_product_id': product.id, 'pending_quantity': amount,
            'pending_uom_id': input_uom.id, 'pending_line_id': selected_id or False,
        })
        return session.env._('Scan the lot or serial number for %s.', product.display_name)
    lot = _lot(session, product, lot_name)
    lines = _candidates(session, product, lot, lot_name, selected_id)
    if not lines and session.kind == 'inventory':
        line = internal(session.env['qing.stock.scan.line']).create({
            'session_id': session.id, 'product_id': product.id, 'uom_id': product.uom_id.id,
            'location_id': session.inventory_location_id.id, 'lot_id': lot.id,
        })
        lines = line
    if not lines:
        raise UserError(session.env._('This product, lot or location is not expected on this transfer.'))
    if len(lines) > 1:
        dimensions = {(l.move_id.id, l.uom_id.id, l.location_id.id, l.location_dest_id.id,
                       l.lot_id.id, l.lot_name, l.owner_id.id, l.package_id.id, l.result_package_id.id) for l in lines}
        if len(dimensions) != 1:
            raise UserError(session.env._('Select the matching operation line to specify its location, lot, owner and package.'))
        # Native serial receipts contain interchangeable empty slots on the same move.
        lines = (lines.filtered(lambda l: l.quantity < l.baseline_quantity) or lines)[:1]
    line = lines
    if line.whole_package:
        raise UserError(session.env._('This package was scanned as a whole. Undo that package event before scanning individual items.'))
    amount = input_uom._compute_quantity(amount, line.uom_id, round=False)
    amount = quantity(session.env, amount, line.uom_id, allow_zero=absolute)
    target = amount if absolute else line.quantity + amount
    if product.tracking == 'serial':
        product_quantity = line.uom_id._compute_quantity(target, product.uom_id, round=False)
        if product_quantity not in (0, 1):
            raise UserError(session.env._('A serial number can represent only one item.'))
        duplicates = session.line_ids.filtered(lambda l: l.id != line.id and l.product_id == product and l.quantity > 0
                                               and (l.lot_id == lot if lot else l.lot_name == lot_name))
        if target and duplicates:
            raise UserError(session.env._('This serial number has already been scanned.'))
    if session.kind == 'picking':
        operation = session.picking_id.picking_type_id
        if operation.qing_scan_source_required and not session.source_confirmed:
            raise UserError(session.env._('Scan the source location first.'))
        if operation.qing_scan_destination_required and not session.destination_confirmed:
            raise UserError(session.env._('Scan the destination location first.'))
        move = line.move_id
        total = sum(l.uom_id._compute_quantity(l.quantity, move.uom_id, round=False)
                    for l in session.line_ids if l.move_id == move)
        increment = line.uom_id._compute_quantity(target - line.quantity, move.uom_id, round=False)
        if move.uom_id.compare(total + increment, move.product_uom_qty) > 0:
            raise UserError(session.env._('The scanned quantity would exceed the transfer demand.'))
        if line.location_id.usage in ('internal', 'transit') and target > line.baseline_quantity + 1e-8:
            raise UserError(session.env._('Reserve sufficient stock on this transfer before scanning.'))
        if lot_name and not line.lot_id and not line.lot_name:
            if line.location_id.usage in ('internal', 'transit'):
                raise UserError(session.env._('The selected lot is not reserved on this line.'))
            line = internal(line).copy({
                'move_line_id': False, 'lot_id': lot.id, 'lot_name': False if lot else lot_name,
                'quantity': 0, 'baseline_quantity': 0,
            })
            target = amount
        if session.destination_id and line.location_id.usage not in ('internal', 'transit'):
            internal(line).write({'location_dest_id': session.destination_id.id})
    internal(line).write({'quantity': target, 'counted': True})
    internal(session).write({'pending_product_id': False, 'pending_uom_id': False,
                             'pending_quantity': 0, 'pending_line_id': False})
    return session.env._('Recorded %(quantity)s %(unit)s of %(product)s.',
                         quantity=amount, unit=line.uom_id.display_name, product=product.display_name)


def package(session, record, confirm=False):
    if session.kind != 'picking':
        raise UserError(session.env._('Count package contents individually using their assigned count lines.'))
    packages = session.env['stock.package'].search([('id', 'child_of', record.id)])
    lines = session.line_ids.filtered(lambda l: l.package_id in packages and l.baseline_quantity > 0)
    if not lines or any(l.quantity or l.whole_package for l in lines):
        raise UserError(session.env._('This package is not reserved here or some of its contents have already been scanned.'))
    quants = session.env['stock.quant'].search([('package_id', 'in', packages.ids), ('quantity', '>', 0),
                                               ('company_id', '=', session.company_id.id)])
    expected = defaultdict(float)
    reserved = defaultdict(float)
    for quant in quants:
        key = (quant.product_id.id, quant.location_id.id, quant.lot_id.id, quant.owner_id.id, quant.package_id.id)
        expected[key] += quant.quantity
    for line in lines:
        key = (line.product_id.id, line.location_id.id, line.lot_id.id, line.owner_id.id, line.package_id.id)
        reserved[key] += line.uom_id._compute_quantity(line.baseline_quantity, line.product_id.uom_id, round=False)
    products = {line.product_id.id: line.product_id for line in lines}
    if set(expected) != set(reserved) or any(
        products[key[0]].uom_id.compare(expected[key], reserved[key]) for key in expected
    ):
        raise UserError(session.env._('The entire package contents must be reserved on this transfer before scanning it as a whole.'))
    if not confirm:
        return {'package_preview': [{'id': l.id, 'product': l.product_id.display_name,
                                     'quantity': l.baseline_quantity, 'unit': l.uom_id.display_name} for l in lines],
                'message': session.env._('Review the package contents and confirm the package scan.')}
    for line in lines:
        add_product(session, line.product_id, line.baseline_quantity, line.uom_id,
                    line.lot_id.name or line.lot_name, line.id)
        internal(line).write({'whole_package': True, 'result_package_id': line.package_id.id})
    return session.env._('Package recorded: %s', record.display_name)


def scan(session, payload):
    kind = payload.get('kind', 'auto')
    if kind not in ('auto', 'product', 'source', 'destination', 'lot', 'package'):
        raise UserError(session.env._('Unknown scan mode.'))
    selected_id = payload.get('line_id') or False
    if selected_id and type(selected_id) is not int:
        raise UserError(session.env._('Select a valid operation line.'))
    code = payload.get('code', '')
    if session.pending_product_id and kind in ('auto', 'lot'):
        if not isinstance(code, str) or not 1 <= len(code.strip()) <= 100:
            raise UserError(session.env._('Scan a valid lot or serial number.'))
        if kind == 'auto':
            if matches(session.env, parse(session.env, code), allow_empty=True):
                raise UserError(session.env._('This code also identifies a product, location or package. Select Lot / Serial explicitly or clear the pending scan.'))
        return add_product(session, session.pending_product_id, session.pending_quantity,
                           session.pending_uom_id, code.strip(), session.pending_line_id.id)
    if kind == 'lot':
        raise UserError(session.env._('Scan a product before its lot or serial number.'))
    if session.pending_product_id:
        raise UserError(session.env._('Complete or clear the pending lot or serial scan first.'))
    if payload.get('product_id'):
        product = scoped_record(session.env, 'product.product', payload['product_id'])
        return add_product(session, product, payload.get('quantity', 1), product.uom_id,
                           payload.get('lot_name', False), selected_id)
    parsed = parse(session.env, code)
    match_kind, record, uom = matches(session.env, parsed, parsed.get('kind', kind))
    if match_kind == 'location':
        return location(session, record, kind)
    if match_kind == 'package':
        return package(session, record, payload.get('confirm_package') is True)
    amount = parsed['amount'] if parsed['gs1'] or parsed['amount'] != 1 else payload.get('quantity', 1)
    return add_product(session, record, amount, parsed.get('weight_uom') or uom, parsed['lot'], selected_id)
