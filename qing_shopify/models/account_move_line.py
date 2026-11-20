from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    shopify_billed_on_delivery = fields.Boolean(copy=False, readonly=True)
    shopify_refund_base_expected = fields.Monetary(copy=False)
    shopify_refund_source_line_id = fields.Many2one('account.move.line', check_company=True, copy=False, ondelete='restrict')
    shopify_refund_tax_expected = fields.Monetary(copy=False)
