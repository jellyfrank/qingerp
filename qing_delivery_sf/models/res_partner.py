from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    sf_monthly_card = fields.Char(
        string='SF Monthly Card',
        help='SF Express monthly settlement card used when this contact is the sender. '
             'Overrides the card of the delivery method.',
    )
