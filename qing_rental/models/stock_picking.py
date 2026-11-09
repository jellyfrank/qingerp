from odoo import api, fields, models, _
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.depends('move_ids.qing_rental_line_id.order_id')
    def _compute_qing_rental_order_ids(self):
        for picking in self:
            picking.qing_rental_order_ids = picking.move_ids.qing_rental_line_id.order_id

    def _create_return(self):
        if self.move_ids.filtered('qing_rental_line_id'):
            raise UserError(_('Use Create Return on the rental order to keep rental quantities and serial numbers consistent.'))
        return super()._create_return()

    qing_rental_order_ids = fields.Many2many(
        'sale.order', string='Rental Orders', compute='_compute_qing_rental_order_ids',
        compute_sudo=True, groups='sales_team.group_sale_salesman',
    )
