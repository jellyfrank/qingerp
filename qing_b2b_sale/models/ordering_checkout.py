from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json

from markupsafe import Markup, escape
from psycopg2.errors import UniqueViolation

from odoo import _, api, Command, models
from odoo.exceptions import ConcurrencyError, ValidationError
from odoo.fields import Domain
from odoo.tools import hash_sign, verify_hash_signed
from odoo.addons.qing_api.api import ApiError

PREVIEW_SCOPE = "qing.b2b.checkout"


def _digest(values):
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class OrderingCheckout(models.AbstractModel):
    _name = "qing.b2b.checkout"
    _description = "Ordering checkout and quotation history"

    @api.model
    def _orders(self, website):
        return self.env["sale.order"].sudo().with_company(website.company_id)

    @api.model
    def _buyer(self, website):
        return self.env["qing.b2b.catalog"]._buyer(website)

    @api.model
    def _own_order_domain(self, website, buyer):
        return Domain([("partner_id", "=", buyer.id), ("company_id", "=", website.company_id.id)])

    @api.model
    def _addresses(self, website, buyer):
        return buyer | self.env["res.partner"].sudo().search([
            ("parent_id", "=", buyer.id), ("type", "=", "delivery"),
            ("company_id", "in", [False, website.company_id.id]),
        ])

    @api.model
    def _address_values(self, address):
        return {"id": address.id, "name": address.name, "display": address.contact_address or address.name}

    @api.model
    def _create_address(self, website, data):
        buyer = self._buyer(website)
        countries = self.env["res.country"].sudo()
        country = countries.search([("code", "=", data["country"])], limit=1)
        if not country:
            raise ApiError("INVALID_COUNTRY", _("Choose a valid country."), 422)
        state = self.env["res.country.state"].sudo().browse(data.get("state") or []).exists()
        if (data.get("state") and not state) or (state and state.country_id != country):
            raise ApiError("INVALID_STATE", _("Choose a state in the address country."), 422)
        if country.zip_applicability == "required" and not data.get("zip") or country.state_required and not state:
            raise ApiError("ADDRESS_INCOMPLETE", _("Complete the required address fields."), 422)
        address = self.env["res.partner"].sudo().with_company(website.company_id).create({
            "parent_id": buyer.id, "type": "delivery", "company_id": website.company_id.id,
            "name": data["name"], "street": data["street"], "city": data["city"],
            "zip": data.get("zip", ""), "country_id": country.id, "state_id": state.id,
        })
        return self._address_values(address)

    @api.model
    def _countries(self):
        countries = self.env["res.country"].sudo().search([], order="name")
        return [{"code": row.code, "name": row.name, "state_required": row.state_required,
                 "zip_required": row.zip_applicability == "required"} for row in countries if row.code]

    @api.model
    def _states(self, country_code):
        states = self.env["res.country.state"].sudo().search([("country_id.code", "=", country_code)])
        return [{"id": row.id, "name": row.name} for row in states]

    @api.model
    def _normalise_lines(self, website, buyer, cart):
        catalog = self.env["qing.b2b.catalog"]
        quantities = defaultdict(int)
        for line in cart:
            quantities[line["product"], line["unit"]] += line["quantity"]
        ids = list({product for product, unit in quantities})
        products = catalog._products(website).search(catalog._domain(website) & Domain("id", "in", ids))
        by_id = {product.id: product for product in products}
        lines = []
        invalid = []
        for (product_id, unit_id), quantity in quantities.items():
            product = by_id.get(product_id)
            unit = catalog._units(product).filtered(lambda item: item.id == unit_id) if product else False
            if not unit or quantity > 100000:
                invalid.append({"code": "INVALID_LINE", "field": str(product_id),
                                "message": _("The product, packaging or quantity is unavailable.")})
            else:
                lines.append((product, unit, quantity))
        if invalid:
            raise ApiError("CART_CHANGED", _("Review the affected items."), 409, details=invalid)
        return self._prepare_lines(website, buyer, lines)

    @api.model
    def _prepare_lines(self, website, buyer, lines):
        return lines

    @api.model
    def _item_discount(self, website, buyer, line):
        return line.discount

    @api.model
    def _stock(self, website, buyer, warehouse, quantities):
        return []

    @api.model
    def _check_trade_rules(self, website, buyer, quotation, quantities):
        return

    @api.model
    def _resolve_cart(self, website, buyer, data):
        address_id = data.get("address") or buyer.id
        address = self._addresses(website, buyer).filtered(lambda item: item.id == address_id)
        if not address:
            raise ApiError("ADDRESS_NOT_FOUND", _("Choose one of your delivery addresses."), 404)
        lines = self._normalise_lines(website, buyer, data["lines"])
        quantities = defaultdict(float)
        for product, unit, qty in lines:
            quantities[product] += unit._compute_quantity(qty, product.uom_id, round=False)
        minimum = [{
            "field": str(product.id), "code": "MINIMUM_QUANTITY",
            "message": _("%(product)s requires at least %(quantity)s %(unit)s.",
                         product=product.display_name, quantity=product.minimum_quantity,
                         unit=product.uom_id.name),
        } for product, quantity in quantities.items()
            if product.uom_id.compare(quantity, product.minimum_quantity) < 0]
        if minimum:
            raise ApiError("MINIMUM_QUANTITY", _("Increase the quantities of the affected items."), 422,
                           details=minimum)
        warehouse = website._b2b_warehouse(buyer)
        notices = self._stock(website, buyer, warehouse, quantities)
        values = {
            "company_id": website.company_id.id,
            "partner_id": buyer.id,
            "partner_shipping_id": address.id,
            "pricelist_id": buyer.property_product_pricelist.id,
            "b2b_website_id": website.id,
            "order_line": [Command.create({
                "product_id": product.id, "product_uom_id": unit.id, "product_uom_qty": quantity,
            }) for product, unit, quantity in lines],
        }
        if warehouse:
            values["warehouse_id"] = warehouse.id
        quotation = self._orders(website).new(values)
        for line in quotation.order_line:
            line.discount = self._item_discount(website, buyer, line)
        self._check_trade_rules(website, buyer, quotation, quantities)
        # A website warehouse and the shipping address are part of the buyer's preview.
        snapshot = {
            "warehouse": warehouse.id if warehouse else False, "address": address_id,
            "currency": quotation.currency_id.name, "tax": quotation.amount_tax,
            "untaxed": quotation.amount_untaxed, "total": quotation.amount_total,
            "notices": notices, "lines": [self._line_values(line) for line in quotation.order_line],
        }
        return quotation, snapshot

    @api.model
    def _line_values(self, line):
        return {
            "product": line.product_id.id, "unit": line.product_uom_id.id,
            "name": line.product_id.display_name, "unit_name": line.product_uom_id.name,
            "quantity": line.product_uom_qty, "unit_price": line.price_unit, "discount": line.discount,
            "subtotal": line.price_subtotal, "tax": line.price_tax, "total": line.price_total,
        }

    @api.model
    def _signature(self, website, buyer, data, snapshot):
        return [website.id, buyer.id, _digest(data), _digest(snapshot),
                [[line["product"], line["unit"], _digest(line)] for line in snapshot["lines"]]]

    @api.model
    def _preview(self, website, data):
        buyer = self._buyer(website)
        quotation, snapshot = self._resolve_cart(website, buyer, data)
        snapshot["preview"] = hash_sign(self.env(su=True), PREVIEW_SCOPE,
                                        self._signature(website, buyer, data, snapshot),
                                        expiration=timedelta(minutes=15))
        return snapshot

    @api.model
    def _submit(self, website, data):
        buyer = self._buyer(website)
        cart = {key: value for key, value in data.items() if key not in ("request_id", "preview", "accept_backorder")}
        request_digest = _digest(cart)
        domain = self._own_order_domain(website, buyer) & Domain("b2b_request_id", "=", data["request_id"])
        duplicate = self._orders(website).search(domain, limit=1)
        if duplicate:
            if duplicate.b2b_checkout_digest != request_digest or duplicate.b2b_website_id != website:
                raise ApiError("REQUEST_CONFLICT", _("This request number already belongs to another submission."), 409)
            return self._order_summary(duplicate)

        quote, snapshot = self._resolve_cart(website, buyer, cart)
        try:
            signed = verify_hash_signed(self.env(su=True), PREVIEW_SCOPE, data["preview"])
        except (ValueError, UnicodeError):
            signed = None
        current = self._signature(website, buyer, cart, snapshot)
        if not signed or signed[:3] != current[:3]:
            raise ApiError("PREVIEW_EXPIRED", _("Refresh your checkout before submitting."), 409)
        if signed != current:
            previous = {(pid, uid): digest for pid, uid, digest in signed[4]}
            changed = [pid for pid, uid, digest in current[4] if previous.get((pid, uid)) != digest]
            affected = set(changed or [line["product"] for line in snapshot["lines"]])
            details = [{"code": "CHANGED_LINE", "field": str(pid),
                        "message": _("Review the updated price or availability.")} for pid in sorted(affected)]
            raise ApiError("CART_CHANGED", _("Review the updated checkout."), 409, details=details)
        if snapshot["notices"] and not data["accept_backorder"]:
            raise ApiError("BACKORDER_CONFIRMATION_REQUIRED", _("Confirm the items that will be supplied later."), 409)

        order = self._create_quote(website, buyer, quote, data, cart, request_digest)
        return self._order_summary(order)

    @api.model
    def _follow_up(self, website, buyer):
        """Return the sales team and the salesperson who follows this buyer's orders, if one is eligible."""
        Team = self.env["crm.team"].sudo().with_company(website.company_id)
        team = website.b2b_sales_team_id or website.salesteam_id
        if not team:
            team = Team.search([("company_id", "in", (False, website.company_id.id))], limit=1)
        salesperson = buyer.user_id or buyer.commercial_partner_id.user_id or team.user_id or website.b2b_salesperson_id
        if not (salesperson and salesperson.active and salesperson._is_internal()
                and website.company_id in salesperson.company_ids):
            salesperson = self.env["res.users"]
        return team, salesperson

    @api.model
    def _create_quote(self, website, buyer, quote, data, cart, request_digest):
        Orders = self._orders(website)
        team, salesperson = self._follow_up(website, buyer)
        if not salesperson:
            raise ApiError("SALESPERSON_NOT_CONFIGURED", _("The merchant needs to configure an eligible salesperson."), 422)
        source = self.env["utm.source"].sudo().search([("name", "=", "Online")], order="id", limit=1)
        if not source:
            source = self.env.ref("qing_b2b_sale.utm_source_b2b_online").sudo()
        vals = {
            "company_id": website.company_id.id, "b2b_website_id": website.id,
            "partner_id": buyer.id, "partner_shipping_id": quote.partner_shipping_id.id,
            "pricelist_id": quote.pricelist_id.id, "fiscal_position_id": quote.fiscal_position_id.id,
            "b2b_checkout_digest": request_digest, "b2b_request_id": data["request_id"],
            "source_id": source.id, "user_id": salesperson.id, "team_id": team.id,
            "note": Markup("<br>").join(escape(cart.get("note", "")).splitlines()),
            "order_line": [Command.create({
                "product_id": line.product_id.id, "product_uom_id": line.product_uom_id.id,
                "product_uom_qty": line.product_uom_qty, "price_unit": line.price_unit,
                "technical_price_unit": line.price_unit, "discount": line.discount,
                "tax_ids": [Command.set(line.tax_ids.ids)],
            }) for line in quote.order_line],
        }
        if "warehouse_id" in quote._fields and quote.warehouse_id:
            vals["warehouse_id"] = quote.warehouse_id.id
        try:
            with self.env.cr.savepoint():
                order = Orders.with_context(mail_create_nosubscribe=True).create(vals)
                order.flush_recordset(["b2b_request_id"])
        except UniqueViolation as error:
            if error.diag.constraint_name != "sale_order_b2b_request_unique":
                raise
            raise ConcurrencyError("Concurrent B2B submission") from error
        if quote.currency_id.compare_amounts(order.amount_total, quote.amount_total):
            raise ValidationError(_("The quotation amount does not match checkout."))
        order.with_context(mail_activity_quick_update=True).activity_schedule(
            "mail.mail_activity_data_todo", user_id=salesperson.id, summary=_("Follow up on mobile order"),
        )
        return order

    @api.model
    def _order_summary(self, order):
        return {"id": order.id, "name": order.name, "state": order.state,
                "date": str(order.date_order), "total": order.amount_total, "currency": order.currency_id.name}

    @api.model
    def _history(self, website, query):
        buyer = self._buyer(website)
        domain = self._own_order_domain(website, buyer)
        if text := query.get("search"):
            domain &= Domain("name", "ilike", text)
        orders = self._orders(website).search(domain, limit=query["limit"] + 1, offset=query["offset"],
                                             order="date_order DESC,id DESC")
        return {"items": [self._order_summary(order) for order in orders[:query["limit"]]],
                "has_more": len(orders) > query["limit"]}

    @api.model
    def _own_order(self, website, order_id):
        buyer = self._buyer(website)
        order = self._orders(website).search(self._own_order_domain(website, buyer) & Domain("id", "=", order_id))
        if not order:
            raise ApiError("ORDER_NOT_FOUND", _("This order is unavailable."), 404)
        return order

    @api.model
    def _repeat(self, website, order_id):
        order = self._own_order(website, order_id)
        buyer = self._buyer(website)
        catalog = self.env["qing.b2b.catalog"]
        sale_lines = order.order_line.filtered(lambda line: not line.display_type and line.product_id)
        products = catalog._products(website).search(catalog._domain(website) & Domain("id", "in", sale_lines.product_id.ids))
        warehouse = website._b2b_warehouse(buyer)
        notices = []
        lines = []
        quantities = defaultdict(float)
        for line in sale_lines:
            product = line.product_id
            if product not in products or line.product_uom_id not in catalog._units(product):
                notices.append({"field": str(product.id), "code": "PRODUCT_UNAVAILABLE",
                                "message": _("%(product)s or its packaging is unavailable.", product=product.display_name)})
            else:
                lines.append({
                    "product": product.id, "unit": line.product_uom_id.id, "name": product.display_name,
                    "unit_name": line.product_uom_id.name, "quantity": line.product_uom_qty,
                })
                quantities[product] += line.product_uom_id._compute_quantity(line.product_uom_qty, product.uom_id, round=False)
        try:
            notices.extend(self._stock(website, buyer, warehouse, quantities))
        except ApiError as error:
            if error.code != "INSUFFICIENT_STOCK":
                raise
            notices.extend(error.details)
        return {"lines": lines, "notices": notices}
