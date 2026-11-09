from odoo import api, models, _
from odoo.exceptions import AccessError

from .common import is_internal


RENTAL_DETAIL_FIELDS = frozenset({
    'move_id', 'picking_id', 'company_id', 'product_id', 'uom_id', 'quantity', 'lot_id',
    'lot_name', 'location_id', 'location_dest_id', 'owner_id', 'package_id', 'result_package_id', 'quant_id',
})


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        if not is_internal(self.env):
            Move = self.env['stock.move']
            for vals in vals_list:
                move = Move.browse(vals.get('move_id'))
                if move.qing_rental_line_id and move.state == 'done':
                    raise AccessError(_('Completed rental operations cannot receive new detailed operations.'))
        details = super().create(vals_list)
        if not is_internal(self.env) and details.filtered(
                lambda detail: detail.move_id.qing_rental_line_id and detail.move_id.state == 'done'):
            raise AccessError(_('Completed rental operations cannot receive new detailed operations.'))
        return details

    def write(self, vals):
        if not is_internal(self.env) and RENTAL_DETAIL_FIELDS.intersection(vals):
            rentals = self.filtered(lambda detail: detail.move_id.qing_rental_line_id)
            if rentals.filtered(lambda detail: detail.move_id.state == 'done'):
                raise AccessError(_('Completed rental operation quantities and serial numbers cannot be edited.'))
            if 'move_id' in vals:
                target = self.env['stock.move'].browse(vals['move_id'])
                if target.qing_rental_line_id and target.state == 'done':
                    raise AccessError(_('Detailed operations cannot be attached to a completed rental transfer.'))
                if target.qing_rental_line_id and any(detail.move_id and detail.move_id != target for detail in self):
                    raise AccessError(_('Existing detailed operations cannot be moved into a rental transfer.'))
                if any(detail.move_id and detail.move_id != target for detail in rentals):
                    raise AccessError(_('Rental detailed operations cannot be reassigned to another transfer.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda detail: detail.move_id.qing_rental_line_id and detail.move_id.state == 'done'):
            raise AccessError(_('Completed rental detailed operations cannot be deleted.'))
        return super().unlink()
