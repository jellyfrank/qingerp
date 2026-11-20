from odoo import fields, models


class ChannelRefund(models.Model):
    _inherit = 'qing.channel.refund'

    accounted = fields.Boolean(copy=False, readonly=True)
    accounted_digest = fields.Char(copy=False, readonly=True)
    credit_note_id = fields.Many2one('account.move', check_company=True, ondelete='restrict', copy=False)
    return_picking_ids = fields.One2many('stock.picking', 'shopify_refund_id', copy=False)
