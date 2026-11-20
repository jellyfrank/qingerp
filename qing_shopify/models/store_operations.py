from datetime import date, timedelta

from odoo import fields, models

from ..services.client import API_VERSION, TOKEN_RENEWAL_WINDOW


class ShopifyStoreOperations(models.Model):
    _inherit = 'qing.channel.store'

    shopify_api_review_on = fields.Date(string='Review Shopify API By', compute='_compute_shopify_operations')
    shopify_api_version = fields.Char(string='Shopify API Version', compute='_compute_shopify_operations')
    shopify_token_state = fields.Selection(
        selection=[('connected', 'Current'), ('due', 'Renewal Due'), ('disconnected', 'Connection Required')],
        string='Shopify Credentials', compute='_compute_shopify_operations',
    )

    def _operations_due_checks(self):
        super()._operations_due_checks()
        if (self.platform == 'shopify' and self.shopify_health == 'connected'
                and self.shopify_api_review_on <= fields.Date.today()):
            self.env['qing.channel.exception']._flag(self, self, 'shopify_api_version_review_due')

    def _compute_shopify_operations(self):
        year, month = (int(item) for item in API_VERSION.split('-'))
        review_on = date(year + 1, month, 1) - timedelta(days=30)
        renew_after = fields.Datetime.now() + TOKEN_RENEWAL_WINDOW
        for store in self:
            shopify = store.platform == 'shopify'
            store.shopify_api_version = API_VERSION if shopify else False
            store.shopify_api_review_on = review_on if shopify else False
            state = False
            if shopify:
                expires = store.sudo().shopify_token_until
                if store.shopify_health != 'connected':
                    state = 'disconnected'
                else:
                    state = 'due' if not expires or expires <= renew_after else 'connected'
            store.shopify_token_state = state
