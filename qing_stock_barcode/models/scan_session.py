import json
import math
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import counting, picking, resolver
from ..services.common import (
    ACTIVE_STATES, SESSION_KINDS, SESSION_STATES, check_operator, digest,
    internal, is_manager, lock_records, quantity, request_key, scoped_record,
)


class ScanSession(models.Model):
    _name = 'qing.stock.scan.session'
    _inherit = 'qing.stock.scan.guard'
    _description = 'Warehouse Scan Session'
    _order = 'id desc'
    _check_company_auto = True

    _active_unique = models.Constraint('UNIQUE(active_key)', 'Another operator is already working on this operation.')
    _open_unique = models.Constraint('UNIQUE(user_id, open_key)', 'This session request has already been used.')

    def _move_progress(self):
        self.ensure_one()
        scanned_by_move = defaultdict(float)
        planned_by_move = {}
        for line in self.line_ids.filtered('move_id'):
            scanned_by_move[line.move_id.id] += line.uom_id._compute_quantity(line.quantity, line.move_id.uom_id, round=False)
            planned_by_move[line.move_id.id] = line.planned_quantity
        moves = []
        for move in self.line_ids.move_id:
            moves.append({'id': move.id, 'product': move.product_id.display_name, 'planned': planned_by_move[move.id],
                          'scanned': scanned_by_move[move.id], 'unit': move.uom_id.display_name})
        return moves

    def _list_progress(self):
        """Summarize already-prefetched sessions without loading event histories."""
        self.mapped('line_ids').fetch(['quantity', 'planned_quantity', 'move_id', 'uom_id', 'counted'])
        result = {}
        for session in self:
            if session.kind == 'inventory':
                rows = [100 if line.counted else 0 for line in session.line_ids]
            else:
                rows = [max(0, min(100, math.floor(move['scanned'] / move['planned'] * 1000) / 10))
                        if move['planned'] > 0 else 0 for move in session._move_progress()]
            result[session.id] = math.floor(sum(rows) / len(rows) * 10) / 10 if rows else 0
        return result

    def _lock(self, write=True):
        self.ensure_one()
        check_operator(self.env)
        self.check_access('write' if write else 'read')
        if self.company_id != self.env.company:
            raise AccessError(self.env._('Switch to the session company before continuing.'))
        if write and self.user_id != self.env.user and not is_manager(self.env):
            raise AccessError(self.env._('This scan session is assigned to another operator.'))
        lock_records(self, serialize=write)

    def _scoped(self):
        return self.with_company(self.company_id).with_context(allowed_company_ids=[self.company_id.id])

    @api.model
    def _start(self, kind, record_id, key, device):
        check_operator(self.env)
        key = request_key(self.env, key)
        if not isinstance(device, str) or len(device) > 100:
            raise UserError(self.env._('The device label must be at most 100 characters.'))
        model = 'stock.picking' if kind == 'picking' else 'stock.location'
        record = scoped_record(self.env, model, record_id, 'write' if kind == 'picking' else 'read')
        lock_records(record, serialize=True)
        domain = [('user_id', '=', self.env.uid), ('open_key', '=', key)]
        old = self.search(domain, limit=1)
        if old:
            target = old.picking_id if kind == 'picking' else old.inventory_location_id
            if old.kind != kind or target != record:
                raise UserError(self.env._('This request UUID was used for a different operation.'))
            return old.get_session()
        active_key = f'{kind}:{record.id}'
        active = self.sudo().search([('active_key', '=', active_key)], limit=1)
        if active:
            if active.user_id.id != self.env.uid:
                raise UserError(self.env._('Another operator is already working on this operation. Ask a scan manager to take over.'))
            return active.sudo(False).get_session()
        values = {
            'kind': kind, 'company_id': self.env.company.id, 'user_id': self.env.uid,
            'active_key': active_key, 'open_key': key, 'device': device,
            'name': record.display_name,
        }
        if kind == 'picking':
            values['picking_id'] = record.id
        else:
            if record.usage != 'internal' or record.company_id != self.env.company:
                raise UserError(self.env._('Select an internal location in the current company.'))
            if self.env['stock.location'].search_count([('location_id', '=', record.id), ('active', '=', True)], limit=1):
                raise UserError(self.env._('Count one leaf location at a time.'))
            values['inventory_location_id'] = record.id
        session = internal(self).create(values)
        (picking if kind == 'picking' else counting).prepare(session)
        return session.get_session()

    def _line_state(self):
        return {line.id: {
            'quantity': line.quantity, 'counted': line.counted,
            'whole_package': line.whole_package, 'result_package_id': line.result_package_id.id,
        } for line in self.line_ids}

    def _undo(self):
        domain = [('session_id', '=', self.id), ('state', '=', 'accepted'), ('undone', '=', False)]
        events = self.env['qing.stock.scan.event'].search(domain, order='id desc', limit=100)
        event = next((e for e in events if e.result.get('changes') and e.payload.get('op') != 'undo'), None)
        if not event:
            raise UserError(self.env._('There is no quantity event to undo.'))
        for change in event.result['changes']:
            line = self.line_ids.filtered(lambda l: l.id == change['id'])
            if not line or self._line_state()[line.id] != change['after']:
                raise UserError(self.env._('The line changed after this event and cannot be undone safely.'))
            internal(line).write(change['before'])
        internal(event).write({'undone': True})
        return {'message': self.env._('The last quantity event was reversed.'), 'reversed_event_id': event.id}

    def _dispatch(self, payload):
        operation = payload.get('op', 'scan')
        if operation == 'return':
            if self.kind != 'picking' or self.picking_id.state != 'done':
                raise UserError(self.env._('Only a completed transfer can be returned.'))
            return {'action': self.picking_id.action_return(), 'message': self.env._('The native return operation is ready.')}
        if operation == 'take_over':
            if not is_manager(self.env) or self.state not in ACTIVE_STATES:
                raise AccessError(self.env._('Only a scan manager can take over an active session.'))
            internal(self).write({'user_id': self.env.uid})
            return {'message': self.env._('Session assigned to you.')}
        if self.state not in ACTIVE_STATES:
            raise UserError(self.env._('This session is closed.'))
        if operation == 'release':
            if self.state != 'applied' or not is_manager(self.env):
                raise AccessError(self.env._('Only a scan manager can release a session awaiting validation.'))
            picking.lock(self.picking_id)
            internal(self).write({'state': 'cancelled', 'active_key': False})
            return {'message': self.env._('Scanner control released. Applied quantities remain on the native transfer; review them in Inventory before proceeding.')}
        if operation == 'finish':
            if self.kind == 'inventory':
                if not is_manager(self.env):
                    raise AccessError(self.env._('Only a scan manager can apply an inventory count.'))
                if self.state != 'review':
                    raise UserError(self.env._('Submit the count for review before applying it.'))
                action = counting.apply(self)
            else:
                action = picking.validate(self)
            return {'action': action, 'message': self.env._('Operation completed.') if self.state == 'done'
                    else self.env._('Complete the native validation dialog to finish the transfer.')}
        if operation == 'cancel':
            if self.state == 'applied':
                raise UserError(self.env._('Quantities have already been applied. Finish validation or review the native transfer.'))
            internal(self).write({'state': 'cancelled', 'active_key': False})
            return {'message': self.env._('Session cancelled. Stock was not changed.')}
        if self.state != 'scanning':
            raise UserError(self.env._('This session is awaiting review or validation.'))
        if self.kind == 'picking':
            picking.check_current(self)
        if operation == 'scan':
            result = resolver.scan(self, payload)
            return result if isinstance(result, dict) else {'message': result}
        if operation == 'set_count':
            if self.kind != 'inventory':
                raise UserError(self.env._('Absolute counts are only available in inventory sessions.'))
            line = self.line_ids.filtered(lambda l: l.id == payload.get('line_id'))
            if not line:
                raise UserError(self.env._('Select a count line.'))
            amount = quantity(self.env, payload.get('quantity'), line.uom_id, allow_zero=True)
            result = resolver.add_product(self, line.product_id, amount, line.uom_id,
                                          line.lot_id.name, line.id, absolute=True)
            return {'message': result}
        if operation == 'undo':
            return self._undo()
        if operation == 'clear_pending':
            internal(self).write({'pending_product_id': False, 'pending_uom_id': False,
                                  'pending_line_id': False, 'pending_quantity': 0})
            return {'message': self.env._('Pending scan cleared.')}
        if operation == 'submit_review':
            if self.kind != 'inventory' or self.pending_product_id or not self.line_ids.filtered('counted'):
                raise UserError(self.env._('Complete at least one count and clear any pending scan first.'))
            internal(self).write({'state': 'review'})
            return {'message': self.env._('Count submitted for review. Uncounted lines will remain unchanged.')}
        raise UserError(self.env._('Unknown scan operation.'))

    def _execute(self, key, payload):
        key = request_key(self.env, key)
        if not isinstance(payload, dict) or len(json.dumps(payload)) > 8000:
            raise UserError(self.env._('Invalid scan request.'))
        allowed = {'op', 'code', 'kind', 'quantity', 'line_id', 'product_id', 'lot_name', 'confirm_package'}
        if set(payload) - allowed:
            raise UserError(self.env._('Unsupported scan request fields.'))
        expected = digest(payload)
        Event = self.env['qing.stock.scan.event']
        previous = Event.search([('session_id', '=', self.id), ('request_key', '=', key)], limit=1)
        if previous:
            if previous.request_hash != expected:
                raise UserError(self.env._('The same request UUID cannot be reused with different content.'))
            result = dict(previous.result)
            if self.state == 'done' and previous.payload.get('op') == 'finish':
                result['action'] = False
            return {'event_id': previous.id, 'ok': previous.state == 'accepted',
                    'result': result, 'session': self.get_session(), 'replayed': True}
        before = self._line_state()
        try:
            with self.env.cr.savepoint():
                result = self._dispatch(payload)
                state = 'accepted'
        except (UserError, ValidationError) as error:
            self.invalidate_recordset()
            result, state = {'message': str(error)}, 'rejected'
        changes = []
        if state == 'accepted':
            for line_id, after in self._line_state().items():
                baseline = before.get(line_id, {'quantity': 0, 'counted': False, 'whole_package': False, 'result_package_id': False})
                if baseline != after:
                    changes.append({'id': line_id, 'before': baseline, 'after': after})
        result['changes'] = changes
        event = internal(Event).create({
            'session_id': self.id, 'request_key': key, 'request_hash': expected,
            'payload': payload, 'result': result, 'state': state,
            'message': result.get('message', ''), 'user_id': self.env.uid, 'device': self.device,
        })
        return {'event_id': event.id, 'ok': state == 'accepted', 'result': result, 'session': self.get_session()}

    active_key = fields.Char(readonly=True, index=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    destination_confirmed = fields.Boolean(readonly=True)
    destination_id = fields.Many2one('stock.location', check_company=True, readonly=True)
    device = fields.Char(readonly=True)
    event_ids = fields.One2many('qing.stock.scan.event', 'session_id', readonly=True)
    inventory_location_id = fields.Many2one('stock.location', check_company=True, readonly=True)
    kind = fields.Selection(SESSION_KINDS, required=True, readonly=True)
    line_ids = fields.One2many('qing.stock.scan.line', 'session_id', readonly=True)
    name = fields.Char(required=True, readonly=True)
    open_key = fields.Char(required=True, readonly=True)
    pending_line_id = fields.Many2one('qing.stock.scan.line', readonly=True)
    pending_product_id = fields.Many2one('product.product', check_company=True, readonly=True)
    pending_quantity = fields.Float(readonly=True)
    pending_uom_id = fields.Many2one('uom.uom', readonly=True)
    picking_id = fields.Many2one('stock.picking', check_company=True, readonly=True, ondelete='restrict')
    snapshot_hash = fields.Char(readonly=True)
    source_confirmed = fields.Boolean(readonly=True)
    source_id = fields.Many2one('stock.location', check_company=True, readonly=True)
    state = fields.Selection(SESSION_STATES, default='scanning', required=True, readonly=True, index=True)
    user_id = fields.Many2one('res.users', required=True, readonly=True, index=True)

    @api.model
    def start_picking(self, picking_id, key, device=''):
        """Claim a confirmed transfer or resume the caller's existing claim."""
        return self._start('picking', picking_id, key, device)

    @api.model
    def start_count(self, location_id, key, device=''):
        """Snapshot a leaf location without staging native inventory quantities."""
        return self._start('inventory', location_id, key, device)

    def execute(self, key, payload):
        """Record one idempotent operation under the caller's company and access rights."""
        self.ensure_one()
        self._lock()
        return self._scoped()._execute(key, payload)

    def get_event(self, key):
        """Reconcile an uncertain request without executing a new operation."""
        self._lock(write=False)
        key = request_key(self.env, key)
        event = self.env['qing.stock.scan.event'].search([('session_id', '=', self.id), ('request_key', '=', key)], limit=1)
        if not event:
            return False
        result = dict(event.result)
        if self.state == 'done' and event.payload.get('op') == 'finish':
            result['action'] = False
        return {'event_id': event.id, 'ok': event.state == 'accepted', 'result': result, 'session': self.get_session()}

    def get_session(self):
        """Return accessible progress, hiding count baselines until review."""
        self.ensure_one()
        check_operator(self.env)
        self.check_access('read')
        if self.company_id != self.env.company:
            raise AccessError(self.env._('Switch to the session company before continuing.'))
        if self.picking_id:
            self.picking_id.check_access('read')
        show_baseline = self.kind == 'picking' or self.state != 'scanning'
        lines = [{
            'id': l.id, 'product_id': l.product_id.id, 'product': l.product_id.display_name,
            'barcode': l.product_id.barcode or '', 'unit': l.uom_id.display_name,
            'quantity': l.quantity, 'baseline': l.baseline_quantity if show_baseline else None,
            'counted': l.counted, 'lot': l.lot_id.name or l.lot_name or '',
            'source': l.location_id.display_name, 'destination': l.location_dest_id.display_name or '',
            'package': l.package_id.display_name or '', 'owner': l.owner_id.display_name or '',
            'whole_package': l.whole_package, 'tracking': l.product_id.tracking,
            'move_id': l.move_id.id,
        } for l in self.line_ids]
        moves = self._move_progress()
        predecessor = self.picking_id.backorder_id
        return {
            'id': self.id, 'name': self.name, 'state': self.state, 'kind': self.kind,
            'company': self.company_id.display_name, 'user': self.user_id.display_name,
            'responsible': (self.picking_id.user_id if self.picking_id else self.user_id).display_name or '',
            'owned': self.user_id == self.env.user, 'manager': is_manager(self.env),
            'picking_id': self.picking_id.id, 'picking_state': self.picking_id.state or '',
            'operation': self.picking_id.picking_type_code or 'inventory',
            'backorder_of': predecessor.name if predecessor and predecessor.has_access('read') else '',
            'validation_blocker': picking.validation_blocker(self) if self.kind == 'picking' else False,
            'backorders': [{'id': p.id, 'name': p.name} for p in self.picking_id.backorder_ids],
            'source': self.source_id.display_name or '', 'destination': self.destination_id.display_name or '',
            'pending_product': self.pending_product_id.display_name or '', 'lines': lines, 'moves': moves,
            'events': [{'id': e.id, 'message': e.message, 'state': e.state, 'undone': e.undone}
                       for e in self.env['qing.stock.scan.event'].search([('session_id', '=', self.id)], limit=20)],
        }

    @api.model
    def get_completed_picking(self, picking_id):
        """Read native completed lines without claiming a session or changing stock."""
        check_operator(self.env)
        transfer = scoped_record(self.env, 'stock.picking', picking_id)
        if transfer.state != 'done':
            raise UserError(self.env._('This transfer is not completed. Refresh the list before continuing.'))
        moves = transfer.move_ids.filtered(lambda move: move.state == 'done')
        native_lines = transfer.move_line_ids.filtered(lambda line: line.move_id in moves)
        moves.check_access('read')
        native_lines.check_access('read')
        quantities = defaultdict(float)
        lines = []
        for line in native_lines:
            quantities[line.move_id.id] += line.uom_id._compute_quantity(line.quantity, line.move_id.uom_id, round=False)
            lines.append({
                'id': line.id, 'move_id': line.move_id.id, 'product_id': line.product_id.id,
                'product': line.product_id.display_name, 'unit': line.uom_id.display_name,
                'quantity': line.quantity, 'source': line.location_id.display_name,
                'destination': line.location_dest_id.display_name, 'lot': line.lot_id.name or line.lot_name or '',
                'package': line.package_id.display_name or '', 'owner': line.owner_id.display_name or '',
                'whole_package': False,
            })
        predecessor = transfer.backorder_id
        return {
            'id': False, 'readonly': True, 'name': transfer.name, 'state': 'done', 'kind': 'picking',
            'picking_id': transfer.id, 'picking_state': 'done', 'operation': transfer.picking_type_code,
            'company': transfer.company_id.display_name, 'responsible': transfer.user_id.display_name or '',
            'owned': False, 'manager': False, 'validation_blocker': False,
            'backorder_of': predecessor.name if predecessor and predecessor.has_access('read') else '',
            'backorders': [{'id': child.id, 'name': child.name} for child in transfer.backorder_ids if child.has_access('read')],
            'source': transfer.location_id.display_name, 'destination': transfer.location_dest_id.display_name,
            'pending_product': '', 'lines': lines, 'events': [],
            'moves': [{'id': move.id, 'product': move.product_id.display_name, 'unit': move.uom_id.display_name,
                       'planned': move.product_uom_qty, 'scanned': quantities[move.id]} for move in moves],
        }

    @api.model
    def dashboard(self, operation='incoming', search='', show_completed=True):
        check_operator(self.env)
        if operation not in ('incoming', 'outgoing', 'internal') or not isinstance(search, str) or type(show_completed) is not bool:
            raise UserError(self.env._('Select a valid operation type.'))
        domain = [('company_id', '=', self.env.company.id), ('picking_type_code', '=', operation)]
        if search:
            domain += ['|', ('name', 'ilike', search[:100]), ('origin', 'ilike', search[:100])]
        Picking = self.env['stock.picking']
        pending_domain = domain + [('state', 'in', ('confirmed', 'assigned', 'waiting'))]
        session_domain = [('company_id', '=', self.env.company.id), ('state', 'in', ACTIVE_STATES),
                          ('picking_id', 'any', pending_domain)]
        sessions = self.search(session_domain, limit=50)
        # Keep resumable work visible before filling the bounded list with unstarted transfers.
        active = Picking.search(pending_domain + [('id', 'in', sessions.picking_id.ids)], order='scheduled_date, id')
        pending = Picking.search(pending_domain + [('id', 'not in', active.ids)], limit=51 - len(active), order='scheduled_date, id')
        completed = Picking.search(domain + [('state', '=', 'done')], limit=11, order='date_done desc, id desc') if show_completed else Picking.browse()
        drafts = Picking.search(domain + [('state', '=', 'draft')], limit=11, order='write_date desc, id desc') if operation == 'internal' else Picking.browse()
        transfers = drafts[:10] + active + pending[:50 - len(active)] + completed[:10]
        progress = sessions._list_progress()
        by_picking = {session.picking_id.id: session for session in sessions}
        return {
            'company': self.env.company.display_name, 'manager': is_manager(self.env),
            'can_create_internal': operation == 'internal' and Picking.has_access('create'),
            'transfers': [{'id': p.id, 'name': p.name, 'origin': p.origin or '', 'partner': p.partner_id.display_name or '',
                           'source': p.location_id.display_name, 'destination': p.location_dest_id.display_name,
                           'responsible': p.user_id.display_name or '', 'state': p.state,
                           'session_id': by_picking[p.id].id if p.id in by_picking else False,
                           'progress': 100 if p.state == 'done' else progress[by_picking[p.id].id] if p.id in by_picking else 0} for p in transfers],
            'truncated': len(active) + len(pending) > 50 or len(completed) > 10 or len(drafts) > 10,
        }

    @api.model
    def find_picking(self, code):
        check_operator(self.env)
        if not isinstance(code, str) or len(code) > 100:
            raise UserError(self.env._('Scan a valid transfer number.'))
        domain = [('company_id', '=', self.env.company.id), ('name', '=', code.strip())]
        records = self.env['stock.picking'].search(domain, limit=2)
        if len(records) != 1:
            raise UserError(self.env._('No unique transfer matches this barcode.'))
        return records.id

    @api.model
    def find_locations(self, search=''):
        check_operator(self.env)
        domain = [('company_id', '=', self.env.company.id), ('usage', '=', 'internal')]
        if search:
            domain += ['|', ('barcode', '=', str(search)[:100]), ('complete_name', 'ilike', str(search)[:100])]
        sessions = self.search([('company_id', '=', self.env.company.id), ('state', 'in', ACTIVE_STATES),
                                ('inventory_location_id', 'any', domain)], limit=50)
        Location = self.env['stock.location']
        active = Location.search(domain + [('id', 'in', sessions.inventory_location_id.ids)])
        locations = active + Location.search(domain + [('id', 'not in', active.ids)], limit=50 - len(active)) if len(active) < 50 else active
        progress = sessions._list_progress()
        by_location = {session.inventory_location_id.id: session for session in sessions}
        return [{'id': loc.id, 'name': loc.display_name, 'barcode': loc.barcode or '',
                 'session_id': by_location[loc.id].id if loc.id in by_location else False,
                 'progress': progress[by_location[loc.id].id] if loc.id in by_location else 0}
                for loc in locations]

    @api.model
    def find_products(self, search=''):
        check_operator(self.env)
        domain = [('is_storable', '=', True), ('company_id', 'in', (False, self.env.company.id))]
        if search:
            domain += ['|', '|', ('barcode', '=', str(search)[:100]),
                       ('default_code', 'ilike', str(search)[:100]), ('name', 'ilike', str(search)[:100])]
        return [{'id': p.id, 'name': p.display_name, 'barcode': p.barcode or ''}
                for p in self.env['product.product'].search(domain, limit=30)]

    @api.model
    def lookup_stock(self, code):
        """Resolve one unambiguous barcode within the active company."""
        check_operator(self.env)
        parsed = resolver.parse(self.env, code)
        kind, record, _unit = resolver.matches(self.env, parsed, 'lookup')
        domain = [('company_id', '=', self.env.company.id), ('location_id.usage', 'in', ('internal', 'transit'))]
        if kind == 'product':
            domain.append(('product_id', '=', record.id))
            if parsed['lot']:
                domain.append(('lot_id.name', '=', parsed['lot']))
        elif kind == 'lot':
            domain.append(('lot_id', '=', record.id))
        elif kind == 'location':
            domain.append(('location_id', 'child_of', record.id))
        else:
            domain.append(('package_id', 'child_of', record.id))
        records = self.env['stock.quant'].search(domain, limit=101)
        return {'truncated': len(records) > 100, 'rows': [{
            'id': q.id, 'product': q.product_id.display_name, 'location': q.location_id.display_name,
            'lot': q.lot_id.name or '', 'package': q.package_id.display_name or '',
            'quantity': q.quantity, 'available': q.available_quantity, 'reserved': q.reserved_quantity,
            'unit': q.product_id.uom_id.display_name,
        } for q in records[:100]]}

    def print_labels(self):
        self.ensure_one()
        check_operator(self.env)
        self.check_access('read')
        return self.line_ids.product_id.action_open_label_layout()

    def action_open_workbench(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.client', 'tag': 'qing_stock_barcode.workbench', 'params': {'session_id': self.id}}
