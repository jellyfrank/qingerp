from odoo import fields, models

KINDS = [('tax', 'Tax'), ('gateway', 'Payment Gateway'), ('shipping', 'Shipping'),
         ('adjustment', 'Refund Adjustment')]


class ChannelMapping(models.Model):
    _name = 'qing.channel.mapping'
    _description = 'Channel Accounting and Delivery Mapping'
    _check_company_auto = True

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    external_key = fields.Char(required=True)
    journal_id = fields.Many2one('account.journal', check_company=True, ondelete='restrict')
    kind = fields.Selection(KINDS, required=True)
    product_id = fields.Many2one('product.product', check_company=True, ondelete='restrict')
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)
    tax_id = fields.Many2one('account.tax', check_company=True, ondelete='restrict')

    _mapping_unique = models.UniqueIndex('(store_id, kind, external_key)')
