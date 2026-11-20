from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    shopify_refund_source_invoice_ids = fields.Many2many(
        'account.move', string='Shopify Source Invoices', compute='_compute_shopify_refund_source_invoice_ids',
    )

    @api.depends('invoice_line_ids.shopify_refund_source_line_id.move_id')
    def _compute_shopify_refund_source_invoice_ids(self):
        for move in self:
            move.shopify_refund_source_invoice_ids = move.invoice_line_ids.shopify_refund_source_line_id.move_id
