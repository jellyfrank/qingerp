from odoo import api, models
from odoo.exceptions import UserError

from .repair_pricing_mixin import PRICING_FIELDS


class StockMove(models.Model):
    _name = 'stock.move'
    _inherit = ['stock.move', 'qing.repair.pricing.mixin']

    def _qing_quantity(self):
        self.ensure_one()
        return self.quantity if self.repair_id.state == 'done' else self.product_uom_qty

    @api.depends('product_uom_qty', 'quantity', 'repair_id.state')
    def _compute_qing_price_subtotal(self):
        return super()._compute_qing_price_subtotal()

    def _create_repair_invoice_line(self):
        direct = self.filtered(lambda move: move.repair_id.qing_pricing_mode == 'direct')
        super(StockMove, self - direct)._create_repair_invoice_line()
        vals_list = []
        for move in direct:
            invoices = move.repair_id.invoice_ids.filtered(lambda invoice: invoice.state == 'draft')
            if move.invoice_line_ids or move.repair_line_type != 'add' or not invoices or move.state == 'cancel':
                continue
            vals_list.append({
                'move_id': invoices[0].id, 'product_id': move.product_id.id,
                'quantity': move._qing_quantity(), 'product_uom_id': move.uom_id.id,
                'stock_move_id': move.id, **move._qing_invoice_values(),
            })
        self.env['account.move.line'].create(vals_list)

    @api.model_create_multi
    def create(self, vals_list):
        repairs = self.env['repair.order'].browse({vals['repair_id'] for vals in vals_list if vals.get('repair_id') and vals.get('repair_line_type') == 'add'})
        for repair in repairs:
            repair._qing_lock()
            repair._qing_check_price_confirmation()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('repair_id'):
            target = self.env['repair.order'].browse(vals['repair_id'])
            target._qing_lock()
            target._qing_check_price_confirmation()
        if 'repair_id' in vals and self.filtered(lambda move: move.repair_id and move.repair_id.id != vals['repair_id']):
            raise UserError(self.env._('Existing repair charges cannot be moved to another repair.'))
        if (PRICING_FIELDS | {'product_uom_qty', 'repair_line_type', 'repair_id'}) & vals.keys():
            self.filtered(lambda move: move.repair_line_type == 'add' or vals.get('repair_line_type') == 'add')._qing_check_editable()
        if 'quantity' in vals:
            self.filtered(lambda move: move.repair_line_type == 'add' and move.repair_id.state == 'done')._qing_check_editable()
        res = super().write(vals)
        if PRICING_FIELDS & vals.keys():
            self._qing_sync_invoice_prices()
        return res

    def unlink(self):
        self.filtered(lambda move: move.repair_line_type == 'add')._qing_check_editable()
        return super().unlink()
