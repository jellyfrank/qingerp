from odoo import api, models
from odoo.exceptions import UserError

from ..services import picking
from ..services.common import ACTIVE_STATES, check_operator, internal, lock_records, quantity, scoped_record


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _qing_mobile_internal(self):
        self.ensure_one()
        check_operator(self.env)
        scoped_record(self.env, 'stock.picking', self.id, 'write')
        if self.picking_type_code != 'internal' or not self.picking_type_id.active:
            raise UserError(self.env._('Select an active internal transfer operation.'))

    def _qing_mobile_form(self):
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Internal transfer'),
            'res_model': 'stock.picking', 'res_id': self.id or False,
            'views': [(self.env.ref('qing_stock_barcode.view_stock_picking_form_mobile_scan').id, 'form')],
            'target': 'current', 'context': {
                **self.env.context, 'default_company_id': self.env.company.id,
                'default_user_id': self.env.uid, 'default_picking_type_id': False,
            },
        }

    @api.model
    def action_qing_new_internal(self):
        check_operator(self.env)
        self.check_access('create')
        domain = [('code', '=', 'internal'), ('active', '=', True), ('company_id', '=', self.env.company.id)]
        operations = self.env['stock.picking.type'].search(domain, limit=2)
        if not operations:
            raise UserError(self.env._('Configure an internal transfer operation in the current company first.'))
        action = self.browse()._qing_mobile_form()
        action['context']['default_picking_type_id'] = operations.id if len(operations) == 1 else False
        return action

    def action_qing_edit_internal(self):
        self._qing_mobile_internal()
        if self.state != 'draft':
            raise UserError(self.env._('This transfer is no longer a draft. Refresh the list.'))
        return self._qing_mobile_form()

    def action_qing_save_draft(self):
        self._qing_mobile_internal()
        if self.state != 'draft':
            raise UserError(self.env._('This transfer is no longer a draft. Refresh the list.'))
        return {
            'type': 'ir.actions.client', 'tag': 'qing_stock_barcode.workbench',
            'params': {'operation': 'internal', 'list_only': True},
        }

    def action_qing_confirm_scan(self):
        self._qing_mobile_internal()
        lock_records(self, serialize=True)
        if self.state in ('done', 'cancel'):
            raise UserError(self.env._('This transfer is already completed or cancelled. Refresh the list.'))
        if self.state == 'draft':
            for location in self.location_id | self.location_dest_id:
                scoped_record(self.env, 'stock.location', location.id)
                if not location.active or location.usage != 'internal':
                    raise UserError(self.env._('Choose active internal source and destination locations.'))
            if not self.location_id or not self.location_dest_id or self.location_id == self.location_dest_id:
                raise UserError(self.env._('Source and destination locations must be different.'))
            if not self.move_ids:
                raise UserError(self.env._('Add at least one product and its planned quantity.'))
            products = self.move_ids.product_id
            products.check_access('read')
            if any(not p.active or p.type != 'consu' or (p.company_id and p.company_id != self.env.company) for p in products):
                raise UserError(self.env._('Select active goods in the current company.'))
            for move in self.move_ids:
                quantity(self.env, move.product_uom_qty, move.uom_id)
                if move.location_id != self.location_id or move.location_dest_id != self.location_dest_id:
                    raise UserError(self.env._('The product locations differ from this transfer. Review them in Inventory.'))
            result = self.action_confirm()
            if isinstance(result, dict):
                return result
            # Explicit confirmation and scan requests reservation, including manual operation types.
            self.action_assign()
        action = self.action_qing_scan()
        action['params']['reservation_warning'] = self.state != 'assigned'
        return action

    def button_validate(self):
        self.check_access('write')
        domain = [('picking_id', 'in', self.ids), ('state', 'in', ACTIVE_STATES)]
        # Discover occupancy only; stock writes and checks always use the real user's environment.
        sessions = self.env['qing.stock.scan.session'].sudo().search(domain).sudo(False)
        for session in sessions:
            session.check_access('write')
            picking.allow_native_validation(session)
        result = super().button_validate()
        for session in sessions:
            if session.picking_id.state == 'done':
                internal(session).write({'state': 'done', 'active_key': False})
            else:
                internal(session).write({'snapshot_hash': picking.snapshot(session.picking_id)})
        return result

    def action_qing_scan(self):
        self.ensure_one()
        check_operator(self.env)
        scoped_record(self.env, 'stock.picking', self.id)
        if self.state in ('draft', 'cancel'):
            raise UserError(self.env._('Confirm this transfer before scanning.'))
        return {
            'type': 'ir.actions.client', 'tag': 'qing_stock_barcode.workbench',
            'params': {'picking_id': self.id, 'operation': self.picking_type_code, 'picking_state': self.state},
        }
