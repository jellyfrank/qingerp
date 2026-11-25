from collections import defaultdict

from odoo import _, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _b2b_shortages(self):
        self.ensure_one()
        quantities = defaultdict(float)
        for line in self.order_line.filtered("product_id.is_storable"):
            product = line.product_id
            quantities[product.id] += line.product_uom_id._compute_quantity(
                line.product_uom_qty, product.uom_id, round=False,
            )
        products = self.env["product.product"].browse(quantities).with_company(self.company_id).with_context(
            warehouse_id=self.warehouse_id.id,
        )
        shortages = []
        for product in products:
            available = max(0, product.free_qty)
            if product.uom_id.compare(quantities[product.id], available) > 0:
                shortages.append(_("%(product)s: %(quantity)s %(unit)s short",
                                   product=product.display_name,
                                   quantity=product.uom_id.round(quantities[product.id] - available),
                                   unit=product.uom_id.name))
        return shortages

    b2b_backorder_date = fields.Date(string="Agreed Backorder Date", copy=False, readonly=True)
    b2b_backorder_quantity = fields.Float(string="Agreed Backorder Quantity", copy=False, readonly=True)

    def action_confirm(self):
        if not self.env.context.get("b2b_acknowledge_shortages"):
            self.check_access("write")
            messages = []
            for order in self.filtered(lambda record: record.b2b_website_id and record.state in ("draft", "sent")):
                if shortages := order._b2b_shortages():
                    messages.append("%s\n%s" % (order.name, "\n".join(shortages)))
            if messages:
                wizard = self.env["qing.b2b.shortage.confirmation"].create({
                    "order_ids": self.ids, "message": "\n\n".join(messages),
                })
                return {
                    "name": _("Insufficient Stock"), "res_model": wizard._name,
                    "type": "ir.actions.act_window", "view_mode": "form", "target": "new",
                    "res_id": wizard.id,
                }
        return super().action_confirm()
