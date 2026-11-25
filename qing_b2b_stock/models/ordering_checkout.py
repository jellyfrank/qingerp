from odoo import _, api, models
from odoo.addons.qing_api.api import ApiError


class OrderingCheckout(models.AbstractModel):
    _inherit = "qing.b2b.checkout"

    @api.model
    def _line_values(self, line):
        values = super()._line_values(line)
        # Checkout previews are unsaved quotations; only confirmed orders have shipments to report.
        if line.order_id.state == "sale" and line.qty_delivered_method == "stock_move":
            values["delivered"] = line.qty_delivered
        return values

    @api.model
    def _order_summary(self, order):
        summary = super()._order_summary(order)
        if order.state == "sale" and order.delivery_status:
            summary["delivery"] = order.delivery_status
        return summary

    @api.model
    def _stock(self, website, buyer, warehouse, quantities):
        products = self.env["product.product"].sudo().with_company(website.company_id).browse(
            [product.id for product in quantities if product.is_storable],
        ).with_context(warehouse_id=warehouse.id)
        notices = []
        blocked = []
        for product in products:
            need = quantities[product]
            available = max(0, product.free_qty)
            if product.uom_id.compare(need, available) <= 0:
                continue
            if website.b2b_stock_display == "exact":
                message = _("%(product)s is short by %(quantity)s %(unit)s.", product=product.display_name,
                            quantity=product.uom_id.round(need - available), unit=product.uom_id.name)
            else:
                message = _("The requested quantity of %(product)s is unavailable.", product=product.display_name)
            notice = {"field": str(product.id), "message": message}
            if product.allow_out_of_stock_order:
                notices.append({**notice, "code": "BACKORDER"})
            else:
                blocked.append({**notice, "code": "INSUFFICIENT_STOCK"})
        if blocked:
            raise ApiError("INSUFFICIENT_STOCK", _("Adjust the quantities of the affected items."), 409, details=blocked)
        return notices
