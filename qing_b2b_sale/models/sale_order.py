from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    _b2b_request_unique = models.Constraint(
        "UNIQUE(company_id, partner_id, b2b_request_id)",
        "This ordering request already has a quotation.",
    )

    b2b_checkout_digest = fields.Char(copy=False, readonly=True)
    b2b_request_id = fields.Char(copy=False, readonly=True)
    b2b_website_id = fields.Many2one("website", string="B2B Ordering Website",
                                    copy=False, readonly=True, ondelete="restrict", index=True)
