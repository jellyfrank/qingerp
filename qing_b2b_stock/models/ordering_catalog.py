from odoo import api, models


class OrderingCatalog(models.AbstractModel):
    _inherit = "qing.b2b.catalog"

    @api.model
    def _quantity_display(self, website, buyer, products):
        warehouse = website._b2b_warehouse(buyer)
        if website.b2b_stock_display == "hidden":
            return {}
        tracked = products.filtered("is_storable").with_context(warehouse_id=warehouse.id)
        if website.b2b_stock_display == "status":
            return {product.id: {"available": product.free_qty > 0} for product in tracked}
        return {product.id: {"quantity": max(0, product.free_qty)} for product in tracked}
