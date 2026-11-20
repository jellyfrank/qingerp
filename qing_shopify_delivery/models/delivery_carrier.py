from odoo import fields, models


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    shopify_tracking_company = fields.Char(
        string='Shopify Tracking Carrier',
        help='Use the Shopify carrier name to show a supported tracking link in Shopify.',
    )
