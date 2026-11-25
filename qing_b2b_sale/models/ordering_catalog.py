from collections import defaultdict

from odoo import _, api, models
from odoo.fields import Domain
from odoo.addons.qing_api.api import ApiError


class OrderingCatalog(models.AbstractModel):
    _name = "qing.b2b.catalog"
    _description = "Buyer ordering catalog"

    @api.model
    def _buyer(self, website):
        return self.env.user._b2b_check_buyer(website).with_company(website.company_id)

    @api.model
    def _products(self, website):
        return self.env["product.product"].sudo().with_company(website.company_id)

    @api.model
    def _domain(self, website):
        return Domain.AND([
            website.with_user(self.env.uid).sudo().sale_product_domain(),
            [("is_published", "=", True), ("active", "=", True), ("type", "!=", "combo")],
        ])

    @api.model
    def _units(self, product):
        return product.uom_id | product.product_tmpl_id.uom_ids | product.extra_uom_ids

    @api.model
    def _quantity_display(self, website, buyer, products):
        return {}

    @api.model
    def _image(self, website, product_id):
        product = self._products(website).search(self._domain(website) & Domain("id", "=", product_id), limit=1)
        if not product:
            raise ApiError("PRODUCT_NOT_FOUND", _("This product is unavailable."), 404)
        return product

    @api.model
    def _list(self, website, query):
        buyer = self._buyer(website)
        domain = self._domain(website)
        if text := query.get("search"):
            domain &= Domain.OR([
                [("name", "ilike", text)], [("barcode", "=", text)],
                [("product_uom_ids.barcode", "=", text)], [("default_code", "=", text)],
            ])
        if category := query.get("category"):
            domain &= Domain("public_categ_ids", "child_of", category)
        client_limit = query["limit"]
        products = self._products(website).search_fetch(
            domain, ["name", "product_tmpl_id", "uom_id", "default_code", "barcode"],
            order="id", limit=client_limit + 1, offset=query["offset"],
        )
        page = products[:client_limit]
        items, currency = self._priced_items(website, buyer, page)
        return {"items": items, "currency": currency.name, "has_more": len(products) > client_limit}

    @api.model
    def _detail(self, website, product_id):
        buyer = self._buyer(website)
        product = self._image(website, product_id)
        items, currency = self._priced_items(website, buyer, product)
        return {**items[0], "currency": currency.name,
                "description": product.description_sale or "", "reference": product.default_code or ""}

    @api.model
    def _priced_items(self, website, buyer, page):
        pricelist = buyer.property_product_pricelist
        currency = pricelist.currency_id or website.company_id.currency_id
        units_by_product = {product.id: self._units(product) for product in page}
        product_ids_by_unit = defaultdict(list)
        for product in page:
            for unit in units_by_product[product.id]:
                product_ids_by_unit[unit].append(product.id)
        prices_by_unit = {
            unit.id: pricelist._compute_price_rule(page.browse(ids), 1.0, uom=unit, currency=currency)
            for unit, ids in product_ids_by_unit.items()
        }
        position = self.env["account.fiscal.position"].sudo().with_company(website.company_id)._get_fiscal_position(
            buyer, buyer,
        )
        displayed = self._quantity_display(website, buyer, page)
        items = []
        for product in page:
            prices = []
            for unit in units_by_product[product.id]:
                raw = prices_by_unit[unit.id][product.id][0]
                product_taxes = product.taxes_id._filter_taxes_by_company(website.company_id)
                price = product._get_tax_included_unit_price_from_price(
                    raw, product_taxes, fiscal_position=position,
                )
                taxes = position.map_tax(product_taxes)
                breakdown = taxes.compute_all(price, currency=currency, quantity=1, product=product, partner=buyer)
                prices.append({"id": unit.id, "name": unit.name, "price": breakdown["total_included"]})
            items.append({
                "id": product.id, "name": product.display_name,
                "image": "/api/v1/b2b/image/%s" % product.id,
                "units": prices, "stock": displayed.get(product.id, {}),
            })
        return items, currency

    @api.model
    def _categories(self, website):
        self._buyer(website)
        categories = self.env["product.public.category"].sudo().search(website.website_domain(), order="sequence,id")
        return [{"id": category.id, "name": category.display_name} for category in categories]
