from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _b2b_profile(self, website):
        profile = super()._b2b_profile(website)
        salesperson = self.env["qing.b2b.checkout"]._follow_up(website, self.sudo().partner_id)[1].sudo()
        if salesperson:
            # The same work contact the native customer portal shows next to an order.
            contact = salesperson.partner_id
            profile["salesperson"] = {"name": contact.name or "", "email": contact.email or "", "phone": contact.phone or ""}
        return profile
