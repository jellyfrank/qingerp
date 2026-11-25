from odoo import _, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.qing_api.api import ApiError


class Website(models.Model):
    _inherit = "website"

    def _b2b_check_ready(self):
        super()._b2b_check_ready()
        try:
            self._b2b_warehouse(self.env["res.partner"])
        except ApiError as error:
            raise ValidationError(error.message) from error

    def _b2b_capabilities(self):
        return [*super()._b2b_capabilities(), "stock"]

    def _b2b_check_warehouse(self, warehouse):
        self.ensure_one()
        if not warehouse or not warehouse.active or warehouse.company_id != self.company_id:
            raise ApiError("WAREHOUSE_NOT_CONFIGURED", _("The merchant needs to configure a supply warehouse."), 409)
        return warehouse

    def _b2b_warehouse(self, buyer):
        return self._b2b_check_warehouse(self.warehouse_id)

    b2b_stock_display = fields.Selection([
        ("exact", "Available Quantity"), ("status", "In Stock or Out of Stock"), ("hidden", "Hidden"),
    ], string="B2B Inventory Display", required=True, default="status")
