from odoo import _, fields, models
from odoo.exceptions import ValidationError


class Website(models.Model):
    _inherit = "website"

    def _b2b_check_ready(self):
        super()._b2b_check_ready()
        if not self.env["res.groups"]._is_feature_enabled("product.group_product_pricelist"):
            raise ValidationError(_("Enable pricelists in Sales settings before opening B2B accounts."))
        if not (self.b2b_salesperson_id or self.b2b_sales_team_id.user_id or self.salesteam_id.user_id):
            raise ValidationError(_("Configure a B2B sales team leader or a fallback salesperson."))

    def _b2b_capabilities(self):
        return [*super()._b2b_capabilities(), "catalog", "orders", "addresses"]

    def _b2b_warehouse(self, buyer):
        return False

    b2b_salesperson_id = fields.Many2one("res.users", string="B2B Fallback Salesperson")
    b2b_sales_team_id = fields.Many2one("crm.team", string="B2B Sales Team", check_company=True)
