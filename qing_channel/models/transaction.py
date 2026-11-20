from odoo import fields, models


class ChannelTransaction(models.Model):
    _name = 'qing.channel.transaction'
    _description = 'Channel Payment Transaction'
    _check_company_auto = True

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    external_id = fields.Char(required=True)
    gateway = fields.Char()
    kind = fields.Char(required=True)
    order_id = fields.Many2one('qing.channel.order', required=True, check_company=True, ondelete='restrict', index=True)
    parent_external_id = fields.Char()
    payment_id = fields.Many2one('account.payment', check_company=True, ondelete='restrict')
    processed_at = fields.Datetime()
    refund_id = fields.Many2one('qing.channel.refund', check_company=True, ondelete='restrict')
    snapshot = fields.Json(groups='qing_channel.group_channel_manager')
    status = fields.Char(required=True)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)

    _external_unique = models.UniqueIndex('(store_id, external_id)')
