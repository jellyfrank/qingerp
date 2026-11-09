from odoo import fields, models


class ScanLine(models.Model):
    _name = 'qing.stock.scan.line'
    _inherit = 'qing.stock.scan.guard'
    _description = 'Warehouse Scan Line'
    _order = 'id'
    _check_company_auto = True

    baseline_quantity = fields.Float(readonly=True, digits='Product Unit')
    company_id = fields.Many2one(related='session_id.company_id', store=True, index=True)
    counted = fields.Boolean(readonly=True)
    location_dest_id = fields.Many2one('stock.location', check_company=True, readonly=True)
    location_id = fields.Many2one('stock.location', check_company=True, required=True, readonly=True)
    lot_id = fields.Many2one('stock.lot', check_company=True, readonly=True)
    lot_name = fields.Char(readonly=True)
    move_id = fields.Many2one('stock.move', check_company=True, readonly=True, ondelete='restrict')
    move_line_id = fields.Many2one('stock.move.line', check_company=True, readonly=True, ondelete='set null')
    owner_id = fields.Many2one('res.partner', readonly=True)
    package_id = fields.Many2one('stock.package', check_company=True, readonly=True)
    planned_quantity = fields.Float(readonly=True, digits='Product Unit')
    product_id = fields.Many2one('product.product', check_company=True, required=True, readonly=True)
    quantity = fields.Float(readonly=True, digits='Product Unit')
    quant_id = fields.Many2one('stock.quant', readonly=True, ondelete='set null')
    result_package_id = fields.Many2one('stock.package', check_company=True, readonly=True)
    session_id = fields.Many2one('qing.stock.scan.session', required=True, readonly=True, index=True, ondelete='restrict')
    uom_id = fields.Many2one('uom.uom', required=True, readonly=True)
    whole_package = fields.Boolean(readonly=True)
