from odoo import http
from odoo.http import request

from odoo.addons.qing_api.api import (
    ApiResponse, ApiSchema, Boolean, DecimalString, Enum, String, api_route,
)

from ..services.product import ProductService


class ProductPath(ApiSchema):
    product_id = String(required=True, pattern=r"^prd_[0-9a-f]{32}$")


class ProductWriteFields(ApiSchema):
    name = String(min_length=1, max_length=256)
    active = Boolean()
    barcode = String(max_length=128, nullable=True)
    default_code = String(max_length=128, nullable=True)
    category_id = String(pattern=r"^pcat_[0-9a-f]{32}$", nullable=True)
    unit_id = String(pattern=r"^uom_[0-9a-f]{32}$")
    type = Enum(("consu", "service", "combo"))
    list_price = DecimalString(max_length=32)
    sale_ok = Boolean()
    purchase_ok = Boolean()


class ProductCreateRequest(ProductWriteFields):
    name = String(required=True, min_length=1, max_length=256)


class ProductResource(ApiSchema):
    id = String(required=True)
    name = String(required=True)
    active = Boolean(required=True)
    barcode = String(required=True, nullable=True)
    default_code = String(required=True, nullable=True)
    category_id = String(required=True, nullable=True)
    unit_id = String(required=True)
    type = Enum(("consu", "service", "combo"), required=True)
    list_price = DecimalString(required=True)
    sale_ok = Boolean(required=True)
    purchase_ok = Boolean(required=True)


class ProductDeleteResponse(ApiSchema):
    id = String(required=True)
    deleted = Boolean(required=True)


class ProductController(http.Controller):
    @api_route(
        "/api/v1/products", methods=["POST"], operation_id="createProduct",
        tags=["Products"], summary="Create a product",
        description="Creates a product template using the API user's Odoo permissions.",
        request_model=ProductCreateRequest, response_model=ProductResource,
        response_status=201,
    )
    def create_product(self, data):
        result = ProductService(request.env).create(data)
        return ApiResponse(result, 201, {"Location": "/api/v1/products/%s" % result["id"]})

    @api_route(
        "/api/v1/products/<string:product_id>", methods=["PATCH"],
        operation_id="updateProduct", tags=["Products"], summary="Update a product",
        path_model=ProductPath, request_model=ProductWriteFields,
        response_model=ProductResource,
    )
    def update_product(self, product_id, data):
        return ProductService(request.env).update(product_id, data)

    @api_route(
        "/api/v1/products/<string:product_id>", methods=["DELETE"],
        operation_id="deleteProduct", tags=["Products"], summary="Delete a product",
        path_model=ProductPath, response_model=ProductDeleteResponse,
    )
    def delete_product(self, product_id):
        return ProductService(request.env).delete(product_id)
