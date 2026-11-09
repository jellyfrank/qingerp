from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


RENTAL_LOCATION_FIELDS = ('qing_rental_stock_location_id', 'qing_rental_out_location_id')


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    @api.constrains(*RENTAL_LOCATION_FIELDS, 'company_id', 'view_location_id', 'lot_stock_id')
    def _check_qing_rental_locations(self):
        for warehouse in self:
            locations = warehouse.qing_rental_stock_location_id | warehouse.qing_rental_out_location_id
            if not locations:
                continue
            if len(locations) != 2:
                raise ValidationError(_('Configure two distinct rental locations together.'))
            for location in locations:
                if not location.active or location.usage != 'internal' or location.company_id != warehouse.company_id:
                    raise ValidationError(_('Rental locations must be active internal locations of this warehouse company.'))
                if not location._child_of(warehouse.view_location_id) or location._child_of(warehouse.lot_stock_id):
                    raise ValidationError(_('Rental locations must be inside the warehouse and outside its normal Stock location.'))
            stock = warehouse.qing_rental_stock_location_id
            out = warehouse.qing_rental_out_location_id
            if stock._child_of(out) or out._child_of(stock):
                raise ValidationError(_('Rental Stock and On Rent must not contain one another.'))

    qing_rental_out_location_id = fields.Many2one(
        'stock.location', string='On Rent Location', check_company=True, copy=False, ondelete='restrict',
        domain="[('usage', '=', 'internal'), ('company_id', '=', company_id)]",
    )
    qing_rental_stock_location_id = fields.Many2one(
        'stock.location', string='Rental Stock Location', check_company=True, copy=False, ondelete='restrict',
        domain="[('usage', '=', 'internal'), ('company_id', '=', company_id)]",
    )

    def action_qing_setup_rental(self):
        """Create separate stock pools without changing any existing inventory."""
        self.check_access('write')
        if not self.env.user.has_group('stock.group_stock_manager'):
            raise AccessError(_('Only an Inventory Administrator can configure rental locations.'))
        Location = self.env['stock.location']
        for warehouse in self.sorted('id'):
            self.env.cr.execute('SELECT id FROM stock_warehouse WHERE id = %s FOR UPDATE', [warehouse.id])
            warehouse.invalidate_recordset(list(RENTAL_LOCATION_FIELDS))
            if warehouse.qing_rental_stock_location_id and warehouse.qing_rental_out_location_id:
                warehouse._check_qing_rental_locations()
                continue
            values = {}
            for field_name, name in zip(RENTAL_LOCATION_FIELDS, [_('Rental Stock'), _('On Rent')]):
                if warehouse[field_name]:
                    continue
                location = Location.create({
                    'name': name, 'usage': 'internal', 'company_id': warehouse.company_id.id,
                    'location_id': warehouse.view_location_id.id,
                })
                values[field_name] = location.id
            warehouse.write(values)
        return True

    def write(self, vals):
        changed = self.filtered(lambda warehouse: any(
            name in vals and vals[name] != warehouse[name].id for name in RENTAL_LOCATION_FIELDS
        ))
        if changed:
            Move = self.env['stock.move'].sudo()
            domain = [('qing_rental_line_id.order_id.warehouse_id', 'in', changed.ids), ('state', '!=', 'cancel')]
            if Move.search_count(domain, limit=1):
                raise ValidationError(_('Rental locations cannot change after rental transfers have been created.'))
        return super().write(vals)
