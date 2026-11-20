from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

KINDS = [('variant', 'Variant'), ('customer', 'Customer'), ('inventory', 'Inventory Item')]


class ChannelBinding(models.Model):
    _name = 'qing.channel.binding'
    _description = 'Channel Record Binding'
    _check_company_auto = True

    @api.constrains('kind', 'product_id', 'partner_id')
    def _check_bound_record(self):
        for record in self:
            if record.kind in ('variant', 'inventory') and not record.product_id:
                raise ValidationError(_('Product and inventory mappings require a product.'))
            if record.kind == 'customer' and not record.partner_id:
                raise ValidationError(_('Customer mappings require a contact.'))

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    external_id = fields.Char(required=True)
    kind = fields.Selection(KINDS, required=True)
    last_quantity = fields.Integer(copy=False)
    location_external_id = fields.Char(required=True, default='-')
    partner_id = fields.Many2one('res.partner', check_company=True, ondelete='restrict')
    product_id = fields.Many2one('product.product', check_company=True, ondelete='restrict', index=True)
    quantity_checked_at = fields.Datetime(copy=False)
    quantity_version = fields.Integer(copy=False)
    stock_paused = fields.Boolean(copy=False)
    stock_hold_reason = fields.Char(copy=False, readonly=True)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)

    _external_unique = models.UniqueIndex('(store_id, kind, external_id, location_external_id)')
