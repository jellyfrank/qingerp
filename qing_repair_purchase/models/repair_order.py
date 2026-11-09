from odoo import api, Command, fields, models
from odoo.exceptions import AccessError, UserError


class RepairOrder(models.Model):
    _inherit = 'repair.order'

    def _qing_purchase_sources(self):
        self.ensure_one()
        parts = self.move_ids.filtered(lambda move: move.repair_line_type == 'add'
            and move.state != 'cancel' and move.product_id.purchase_ok and move.product_uom_qty > 0
            and not move.created_purchase_line_ids.filtered(lambda line:
                line.order_id.state != 'cancel' and not line.qing_repair_move_id))
        services = self.repair_service_line_ids.filtered(lambda line: line.product_id.purchase_ok and line.quantity > 0)
        return [*parts, *services]

    def _qing_arranged_purchase(self):
        self.ensure_one()
        domain = [('order_id.qing_repair_id', '=', self.id), ('order_id.state', '!=', 'cancel')]
        lines = self.env['purchase.order.line'].search(domain)
        arranged = {}
        for line in lines:
            source = line.qing_repair_move_id or line.qing_repair_service_line_id
            if source:
                uom = source.uom_id
                key = (source._name, source.id)
                arranged[key] = arranged.get(key, 0) + line.uom_id._compute_quantity(line.product_qty, uom)
        return arranged

    def _qing_check_purchase_access(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise AccessError(self.env._('Purchase access is required to create repair RFQs.'))
        if self.company_id not in self.env.companies or self.state == 'cancel':
            raise UserError(self.env._('Select a non-cancelled repair in an allowed company.'))

    @api.depends('move_ids.created_purchase_line_ids.order_id', 'qing_purchase_order_ids')
    def _compute_purchase_count(self):
        for repair in self:
            repair.purchase_count = len(repair.qing_purchase_order_ids | repair.move_ids.created_purchase_line_ids.order_id)

    qing_purchase_order_ids = fields.One2many('purchase.order', 'qing_repair_id', string='Repair Purchases')
    qing_purchase_revision = fields.Integer(default=0, readonly=True, copy=False)

    def action_qing_create_purchase(self):
        self._qing_check_purchase_access()
        arranged = self._qing_arranged_purchase()
        values = []
        for source in self._qing_purchase_sources():
            is_part = source._name == 'stock.move'
            quantity = source.product_uom_qty if is_part else source.quantity
            uom = source.uom_id
            remaining = max(quantity - arranged.get((source._name, source.id), 0), 0)
            if uom.is_zero(remaining):
                continue
            values.append(Command.create({
                'repair_move_id' if is_part else 'repair_service_line_id': source.id,
                'product_id': source.product_id.id, 'quantity': remaining, 'uom_id': uom.id,
            }))
        if not values:
            raise UserError(self.env._('There are no unarranged purchasable repair lines.'))
        wizard = self.env['qing.repair.purchase.wizard'].create({'repair_id': self.id, 'line_ids': values})
        return {'name': self.env._('Create Repair Purchase'), 'type': 'ir.actions.act_window',
            'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new'}

    def action_view_purchase_orders(self):
        self.check_access('read')
        orders = self.qing_purchase_order_ids | self.move_ids.created_purchase_line_ids.order_id
        return {'name': self.env._('Repair Purchases'), 'type': 'ir.actions.act_window',
            'res_model': 'purchase.order', 'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)], 'context': {'create': False}}
