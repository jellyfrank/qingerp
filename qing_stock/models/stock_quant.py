from odoo import api, models
from odoo.exceptions import UserError


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _update_available_quantity(self, product_id, location_id, quantity=False, reserved_quantity=False,
                                   lot_id=None, package_id=None, owner_id=None, in_date=None):
        check_negative = (
            quantity and product_id.uom_id.compare(quantity, 0) < 0
            and location_id.usage in ('internal', 'transit')
            and product_id.is_storable
            and self.env['ir.config_parameter'].sudo().get_bool(
                'qing_stock.prevent_negative_stock', default=True)
            and not self.env.user.sudo().qing_allow_negative_stock
        )
        if check_negative:
            # A row update gives concurrent deductions of this product an MVCC
            # conflict under Odoo's repeatable-read transaction isolation. A
            # row lock alone would leave the waiting transaction's quant view stale.
            self.env.cr.execute('UPDATE product_product SET id = id WHERE id = %s', [product_id.id])
        result = super()._update_available_quantity(
            product_id, location_id, quantity=quantity, reserved_quantity=reserved_quantity,
            lot_id=lot_id, package_id=package_id, owner_id=owner_id, in_date=in_date,
        )
        if not check_negative:
            return result

        # Check physical quantity after the native quant write, without subtracting
        # reservations. For a tracked lot, Odoo may compensate from an untracked
        # quant in the same package and owner.
        quants = self.sudo()._gather(
            product_id, location_id, lot_id=lot_id, package_id=package_id,
            owner_id=owner_id, strict=True,
        )
        physical_quantity = sum(quants.mapped('quantity'))
        if product_id.uom_id.compare(physical_quantity, 0) < 0:
            raise UserError(self.env._(
                'Insufficient stock for %(product)s at %(location)s. '
                'This operation would create negative stock. Contact an administrator if an exception is required.',
                product=product_id.display_name, location=location_id.display_name,
            ))
        return result
