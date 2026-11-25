import base64

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from odoo.addons.qing_api.api import ApiError, api_route
from odoo.addons.qing_b2b_base.constants import API_PATH, B2B_AUTH, B2B_SCOPE
from odoo.addons.qing_b2b_base.controllers.ordering import TAGS, buyer_website

from . import schemas


class B2BSalesController(http.Controller):
    @api_route(API_PATH + "/catalog", methods=["GET"], operation_id="b2bListCatalog",
               tags=TAGS, summary="List published variants and buyer prices",
               query_model=schemas.CatalogQuery, response_model=schemas.CatalogResponse, **B2B_AUTH)
    def catalog(self, query):
        return request.env["qing.b2b.catalog"]._list(buyer_website(), query)

    @api_route(API_PATH + "/catalog/<int:id>", methods=["GET"], operation_id="b2bGetProduct",
               tags=TAGS, summary="Read a published variant and its packaging prices",
               path_model=schemas.Identifier, response_model=schemas.ProductDetail, **B2B_AUTH)
    def product(self, id):
        return request.env["qing.b2b.catalog"]._detail(buyer_website(), id)

    @api_route(API_PATH + "/categories", methods=["GET"], operation_id="b2bListCategories",
               tags=TAGS, summary="List website product categories",
               response_model=schemas.CategoriesResponse, **B2B_AUTH)
    def categories(self):
        return {"items": request.env["qing.b2b.catalog"]._categories(buyer_website())}

    @http.route(API_PATH + "/image/<int:product_id>", type="http", auth="bearer", bearer_scope=B2B_SCOPE,
                methods=["GET"], save_session=False, readonly=True)
    def image(self, product_id):
        if not request.httprequest.headers.get("Authorization", "").lower().startswith("bearer "):
            return request.not_found()
        try:
            product = request.env["qing.b2b.catalog"]._image(buyer_website(), product_id)
        except (ApiError, AccessError):
            return request.not_found()
        stream = request.env["ir.binary"]._get_image_stream_from(product, "image_256")
        return stream.get_response(max_age=300)

    @api_route(API_PATH + "/addresses", methods=["GET"], operation_id="b2bListAddresses",
               tags=TAGS, summary="List the buyer's delivery addresses",
               response_model=schemas.AddressesResponse, **B2B_AUTH)
    def addresses(self):
        website = buyer_website()
        service = request.env["qing.b2b.checkout"]
        buyer = service._buyer(website)
        return {"items": [service._address_values(address) for address in service._addresses(website, buyer)]}

    @api_route(API_PATH + "/addresses", methods=["POST"], operation_id="b2bCreateAddress",
               tags=TAGS, summary="Create a delivery address for the buyer",
               request_model=schemas.AddressInput, response_model=schemas.Address,
               max_body_bytes=4096, **B2B_AUTH)
    def create_address(self, data):
        return request.env["qing.b2b.checkout"]._create_address(buyer_website(), data)

    @api_route(API_PATH + "/countries", methods=["GET"], operation_id="b2bListCountries",
               tags=TAGS, summary="List countries for delivery addresses",
               response_model=schemas.CountriesResponse, **B2B_AUTH)
    def countries(self):
        buyer_website()
        return {"items": request.env["qing.b2b.checkout"]._countries()}

    @api_route(API_PATH + "/states", methods=["GET"], operation_id="b2bListStates",
               tags=TAGS, summary="List states in a delivery country",
               query_model=schemas.CountryQuery, response_model=schemas.CategoriesResponse, **B2B_AUTH)
    def states(self, query):
        buyer_website()
        return {"items": request.env["qing.b2b.checkout"]._states(query["country"])}

    @api_route(API_PATH + "/preview", methods=["POST"], operation_id="b2bPreviewCheckout",
               tags=TAGS, summary="Price and check the cart against the buyer's supply warehouse",
               request_model=schemas.CartRequest, response_model=schemas.CheckoutResponse,
               max_body_bytes=64000, **B2B_AUTH)
    def preview(self, data):
        return request.env["qing.b2b.checkout"]._preview(buyer_website(), data)

    @api_route(API_PATH + "/orders", methods=["POST"], operation_id="b2bSubmitOrder",
               tags=TAGS, summary="Submit the reviewed cart as a single draft quotation",
               request_model=schemas.QuotationRequest, response_model=schemas.OrderSummary,
               max_body_bytes=96000, **B2B_AUTH)
    def submit(self, data):
        data["request_id"] = str(data["request_id"])
        return request.env["qing.b2b.checkout"]._submit(buyer_website(), data)

    @api_route(API_PATH + "/orders", methods=["GET"], operation_id="b2bListOrders",
               tags=TAGS, summary="List orders and quotations belonging to the buyer",
               query_model=schemas.PageQuery, response_model=schemas.OrdersResponse, **B2B_AUTH)
    def history(self, query):
        return request.env["qing.b2b.checkout"]._history(buyer_website(), query)

    @api_route(API_PATH + "/orders/<int:id>", methods=["GET"], operation_id="b2bGetOrder",
               tags=TAGS, summary="Read an order belonging to the buyer",
               path_model=schemas.Identifier, response_model=schemas.OrderDetail, **B2B_AUTH)
    def order(self, id):
        service = request.env["qing.b2b.checkout"]
        order = service._own_order(buyer_website(), id)
        return {**service._order_summary(order),
                "lines": [service._line_values(line) for line in order.order_line if line.product_id]}

    @api_route(API_PATH + "/orders/<int:id>/repeat", methods=["GET"], operation_id="b2bRepeatOrder",
               tags=TAGS, summary="Build a current cart from the buyer's order",
               path_model=schemas.Identifier, response_model=schemas.RepeatResponse, **B2B_AUTH)
    def repeat(self, id):
        return request.env["qing.b2b.checkout"]._repeat(buyer_website(), id)

    @api_route(API_PATH + "/orders/<int:id>/document", methods=["GET"], operation_id="b2bGetOrderDocument",
               tags=TAGS, summary="Download the buyer's quotation PDF",
               path_model=schemas.Identifier, response_model=schemas.DocumentResponse, **B2B_AUTH)
    def document(self, id):
        order = request.env["qing.b2b.checkout"]._own_order(buyer_website(), id)
        pdf, _ = order.env["ir.actions.report"]._render_qweb_pdf("sale.action_report_saleorder", res_ids=order.ids)
        return {"name": order.name + ".pdf", "pdf": base64.b64encode(pdf).decode()}
