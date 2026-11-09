from odoo import fields, models
from .sms_sms import PROVIDERS


class SmsTemplate(models.Model):
    _inherit = "sms.template"

    service_provider = fields.Selection(PROVIDERS, default="default", string="Provider")
