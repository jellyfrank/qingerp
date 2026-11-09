from odoo import api, fields, models


class SaleDeliveryPhoto(models.Model):
    _name = 'qing.sale.delivery.photo'
    _description = 'Sales Delivery Photo'
    _inherit = 'image.mixin'
    _order = 'sequence, id'
    _check_company_auto = True

    company_id = fields.Many2one(
        related='order_id.company_id', store=True, index=True,
    )
    image_1920 = fields.Image(
        string='Photo', required=True, max_width=1600, max_height=1600,
    )
    name = fields.Char(string='Caption', default=lambda self: self.env._('Delivery Photo'), required=True)
    order_id = fields.Many2one(
        'sale.order', string='Sales Order', required=True, ondelete='cascade',
        index=True, check_company=True, copy=False,
    )
    sequence = fields.Integer(default=10)

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['sale.order'].browse({
            vals.get('order_id') or self.env.context.get('default_order_id')
            for vals in vals_list
        } - {None, False})
        orders.check_access('write')
        return super().create(vals_list)

    def write(self, vals):
        orders = self.order_id
        if vals.get('order_id'):
            orders |= self.env['sale.order'].browse(vals['order_id'])
        orders.check_access('write')
        return super().write(vals)

    def unlink(self):
        self.order_id.check_access('write')
        return super().unlink()
