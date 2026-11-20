from odoo import _, api, fields, models
from odoo.exceptions import AccessError


OPENING_FIELDS = {'shopify_opening_stock_covered_qty', 'shopify_opening_fulfilled_qty'}


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (
            any(f'default_{field}' in self.env.context for field in OPENING_FIELDS)
            or any(OPENING_FIELDS & vals.keys() for vals in vals_list)
        ):
            raise AccessError(_('Shopify opening quantities are managed by the channel.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and OPENING_FIELDS & vals.keys():
            raise AccessError(_('Shopify opening quantities are managed by the channel.'))
        return super().write(vals)

    @api.depends('shopify_unbilled_refund_qty', 'order_id.shopify_historical_finance')
    def _compute_qty_to_invoice(self):
        super()._compute_qty_to_invoice()
        affected = self.filtered(lambda line: line.shopify_unbilled_refund_qty or line.order_id.shopify_historical_finance)
        for line in affected:
            remaining = line.product_uom_qty - line.qty_invoiced - line.shopify_unbilled_refund_qty
            if line.order_id.shopify_historical_finance and line.state == 'sale' and not line.display_type:
                line.qty_to_invoice = max(0, remaining)
            elif line.shopify_unbilled_refund_qty and line.qty_to_invoice > 0:
                line.qty_to_invoice = min(line.qty_to_invoice, max(0, remaining))

    @api.depends('shopify_opening_fulfilled_qty')
    def _compute_qty_delivered(self):
        super()._compute_qty_delivered()

    def _prepare_qty_delivered(self):
        quantities = super()._prepare_qty_delivered()
        for line in self.filtered('shopify_opening_fulfilled_qty'):
            quantities[line] += line.shopify_opening_fulfilled_qty
        return quantities

    def _get_qty_procurement(self, previous_product_uom_qty=False):
        quantity = super()._get_qty_procurement(previous_product_uom_qty)
        return quantity + self.shopify_opening_stock_covered_qty

    shopify_expected_base = fields.Monetary(copy=False)
    shopify_expected_tax = fields.Monetary(copy=False)
    shopify_line_key = fields.Char(copy=False)
    shopify_opening_stock_covered_qty = fields.Float(copy=False, readonly=True, digits='Product Unit')
    shopify_opening_fulfilled_qty = fields.Float(copy=False, readonly=True, digits='Product Unit')
    shopify_unbilled_refund_qty = fields.Float(copy=False, readonly=True, digits='Product Unit')
