from odoo import fields, models


class ChannelStockClaim(models.Model):
    _name = 'qing.channel.stock.claim'
    _description = 'Channel Inventory Commitment'
    _check_company_auto = True

    cancelled_qty = fields.Integer(default=0)
    company_id = fields.Many2one(related='line_id.company_id', store=True, precompute=True, index=True)
    location_uncertain = fields.Boolean(copy=False)
    line_id = fields.Many2one('qing.channel.order.line', required=True, ondelete='cascade', index=True)
    opening_fulfilled_qty = fields.Integer(default=0)
    ordered_qty = fields.Integer(required=True)
    product_id = fields.Many2one(related='line_id.product_id', store=True, index=True)
    restocked_qty = fields.Integer(default=0)
    store_id = fields.Many2one(related='line_id.order_id.store_id', store=True, precompute=True, index=True)

    _line_unique = models.UniqueIndex('(line_id)')
