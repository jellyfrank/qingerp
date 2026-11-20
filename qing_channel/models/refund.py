from odoo import fields, models


class ChannelRefund(models.Model):
    _name = 'qing.channel.refund'
    _description = 'Channel Refund'
    _check_company_auto = True

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    external_id = fields.Char(required=True)
    order_id = fields.Many2one('qing.channel.order', required=True, check_company=True, ondelete='restrict', index=True)
    processed_at = fields.Datetime()
    snapshot = fields.Json(groups='qing_channel.group_channel_manager')
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)
    transaction_ids = fields.One2many('qing.channel.transaction', 'refund_id')

    _external_unique = models.UniqueIndex('(store_id, external_id)')
