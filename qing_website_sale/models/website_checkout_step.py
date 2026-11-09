from odoo import models


class WebsiteCheckoutStep(models.Model):
    _inherit = 'website.checkout.step'

    def validate_checkout_progress(self, step_href, order_sudo, **kwargs):
        if self.env.website._qing_is_price_hidden():
            return self.env.website._qing_price_login_url()
        return super().validate_checkout_progress(step_href, order_sudo, **kwargs)
