from odoo import fields, models


class ShopifyShipment(models.Model):
    _name = 'qing.shopify.shipment'
    _description = 'Shopify Shipment'
    _check_company_auto = True

    carrier = fields.Char(readonly=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True, precompute=True, index=True)
    order_id = fields.Many2one('qing.channel.order', required=True, ondelete='restrict', index=True)
    picking_id = fields.Many2one('stock.picking', required=True, ondelete='restrict', check_company=True)
    remote_id = fields.Char(readonly=True)
    tracking = fields.Char(readonly=True)
    tracking_url = fields.Char(readonly=True)
    version = fields.Integer(default=1, readonly=True)

    _picking_unique = models.UniqueIndex('(picking_id)')
    _fulfillment_unique = models.UniqueIndex('(remote_id) WHERE remote_id IS NOT NULL')
