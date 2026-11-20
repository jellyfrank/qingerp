from odoo import api, fields, models


class ChannelOrderLine(models.Model):
    _name = 'qing.channel.order.line'
    _description = 'Channel Order Line'
    _check_company_auto = True

    @api.depends('snapshot', 'product_id.display_name')
    def _compute_channel_product_name(self):
        for line in self:
            line.channel_product_name = (line.snapshot or {}).get('name') or line.product_id.display_name

    channel_product_name = fields.Char(
        string='Channel Product', compute='_compute_channel_product_name', compute_sudo=True,
    )
    company_id = fields.Many2one(related='order_id.company_id', store=True, precompute=True, index=True)
    external_id = fields.Char(required=True)
    order_id = fields.Many2one('qing.channel.order', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', check_company=True, ondelete='restrict')
    quantity = fields.Integer()
    sale_line_id = fields.Many2one('sale.order.line', check_company=True, ondelete='set null', index=True)
    sku = fields.Char()
    snapshot = fields.Json(groups='qing_channel.group_channel_manager')
    variant_id = fields.Char()

    _external_unique = models.UniqueIndex('(order_id, external_id)')
