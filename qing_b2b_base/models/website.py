from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Website(models.Model):
    _inherit = "website"

    def _b2b_check_ready(self):
        self.ensure_one()
        if not self.b2b_enabled:
            raise ValidationError(_("Enable B2B ordering on this website first."))

    def _b2b_capabilities(self):
        self.ensure_one()
        return []

    def _b2b_theme(self):
        self.ensure_one()
        return {}

    @api.constrains("b2b_device_days")
    def _check_b2b_device_days(self):
        if any(not 1 <= website.b2b_device_days <= 365 for website in self):
            raise ValidationError(_("Choose a device lifetime between 1 and 365 days."))

    b2b_cors_origins = fields.Text(string="Additional B2B Frontend Origins",
                                  help="One exact frontend origin per line, including the scheme.")
    b2b_delivery_note = fields.Char(string="B2B Delivery Information", translate=True)
    b2b_device_days = fields.Integer(string="B2B Remember Device Days", default=90, required=True)
    b2b_enabled = fields.Boolean(string="Enable B2B Ordering")
    b2b_payment_note = fields.Char(string="B2B Payment Information", translate=True)
