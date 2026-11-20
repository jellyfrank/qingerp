from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (
            'default_shopify_historical_finance' in self.env.context
            or any('shopify_historical_finance' in vals for vals in vals_list)
        ):
            raise AccessError(_('Use the Shopify financial backfill action for historical sales.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and 'shopify_historical_finance' in vals:
            raise AccessError(_('The Shopify financial backfill flag is managed by the channel.'))
        return super().write(vals)

    shopify_commercial_data = fields.Json(copy=False, groups='qing_channel.group_channel_manager')
    shopify_historical_takeover = fields.Boolean(copy=False, readonly=True)
    shopify_historical_finance = fields.Boolean(copy=False, readonly=True)
    shopify_started_with_refunds = fields.Boolean(copy=False, readonly=True)
    shopify_snapshot_version = fields.Integer(copy=False)
