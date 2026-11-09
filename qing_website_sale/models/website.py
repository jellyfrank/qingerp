from urllib.parse import urlencode

from odoo import api, fields, models
from odoo.http import request


class Website(models.Model):
    _inherit = 'website'

    def _qing_is_price_hidden(self):
        """Apply the price policy only to the selected website's public visitors."""
        return bool(self and self.qing_login_to_see_price and self.env.user._is_public())

    def _qing_price_login_url(self, signup=False):
        redirect = request.httprequest.full_path.rstrip('?') if request else '/shop'
        path = '/web/signup' if signup else '/web/login'
        return f'{path}?{urlencode({"redirect": redirect})}'

    qing_login_to_see_price = fields.Boolean(
        string='Login to See Product Prices',
        default=False,
        help='Hide shop prices from visitors until they log in. Products remain visible.',
    )

    @api.model
    def is_view_active(self, key):
        website = self or self.env.website
        if key == 'website_sale.filter_products_price' and website._qing_is_price_hidden():
            return False
        return super().is_view_active(key)
