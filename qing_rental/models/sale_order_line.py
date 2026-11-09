from odoo import api, fields, models
from odoo.exceptions import UserError

from .common import is_internal


CHARGE_TYPES = [('rent', 'Rental'), ('renewal', 'Renewal')]


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    qing_rental_billed_end = fields.Datetime(readonly=True, copy=False)
    qing_rental_billed_start = fields.Datetime(readonly=True, copy=False)
    qing_rental_charge = fields.Selection(CHARGE_TYPES, readonly=True, copy=False)
    qing_rental_line_id = fields.Many2one('qing.rental.line', readonly=True, ondelete='restrict', copy=False, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not is_internal(self.env) and any(vals.get('qing_rental_line_id', self.env.context.get('default_qing_rental_line_id')) or vals.get('qing_rental_charge', self.env.context.get('default_qing_rental_charge')) for vals in vals_list):
            raise UserError(self.env._('Rental charges must be created by the rental workflow.'))
        return super().create(vals_list)

    def write(self, vals):
        if not is_internal(self.env):
            if {'qing_rental_line_id', 'qing_rental_charge', 'qing_rental_billed_start', 'qing_rental_billed_end'}.intersection(vals):
                raise UserError(self.env._('Rental charge links cannot be changed.'))
            if self.filtered('qing_rental_line_id') and {'product_id', 'product_uom_id', 'product_uom_qty', 'price_unit', 'order_id'}.intersection(vals):
                raise UserError(self.env._('Change the rental equipment rate and update quotation prices instead.'))
            if 'discount' in vals and self.filtered(lambda line: line.qing_rental_line_id and line.order_id.state == 'sale'):
                raise UserError(self.env._('The agreed rental discount is locked after confirmation.'))
        return super().write(vals)

    def unlink(self):
        if not is_internal(self.env) and self.filtered('qing_rental_line_id'):
            raise UserError(self.env._('Remove the rental equipment line instead of its generated charge.'))
        return super().unlink()
