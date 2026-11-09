from odoo.exceptions import AccessError
from odoo.http import request, route

from odoo.addons.website_sale.controllers.cart import Cart as WebsiteSaleCart


class Cart(WebsiteSaleCart):
    @route()
    def cart(self, id=None, access_token=None, **post):
        website = self.env.website
        if website._qing_is_price_hidden():
            return request.redirect(website._qing_price_login_url())
        return super().cart(id=id, access_token=access_token, **post)

    @route()
    def add_to_cart(self, *args, **kwargs):
        if self.env.website._qing_is_price_hidden():
            raise AccessError(self.env._('Log in to see prices and add products to your cart.'))
        return super().add_to_cart(*args, **kwargs)

    @route()
    def update_cart(self, line_id, quantity, product_id=None, **kwargs):
        if self.env.website._qing_is_price_hidden():
            raise AccessError(self.env._('Log in to see prices and add products to your cart.'))
        return super().update_cart(line_id, quantity, product_id=product_id, **kwargs)
