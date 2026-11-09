from odoo import http
from odoo.http import request

from odoo.addons.qing_api.api import ApiResponse, ApiSchema, Boolean, String, api_route

from ..services.product_category import ProductCategoryService


class ProductCategoryPath(ApiSchema):
    category_id = String(required=True, pattern=r"^pcat_[0-9a-f]{32}$")


class ProductCategoryWriteFields(ApiSchema):
    name = String(min_length=1, max_length=256)
    parent_id = String(pattern=r"^pcat_[0-9a-f]{32}$", nullable=True)


class ProductCategoryCreateRequest(ProductCategoryWriteFields):
    name = String(required=True, min_length=1, max_length=256)


class ProductCategoryResource(ApiSchema):
    id = String(required=True)
    name = String(required=True)
    parent_id = String(required=True, nullable=True)


class ProductCategoryDeleteResponse(ApiSchema):
    id = String(required=True)
    deleted = Boolean(required=True)


class ProductCategoryController(http.Controller):
    @api_route(
        "/api/v1/product-categories", methods=["POST"],
        operation_id="createProductCategory", tags=["Product Categories"],
        summary="Create a product category",
        request_model=ProductCategoryCreateRequest,
        response_model=ProductCategoryResource, response_status=201,
    )
    def create_category(self, data):
        result = ProductCategoryService(request.env).create(data)
        return ApiResponse(result, 201, {"Location": "/api/v1/product-categories/%s" % result["id"]})

    @api_route(
        "/api/v1/product-categories/<string:category_id>", methods=["PATCH"],
        operation_id="updateProductCategory", tags=["Product Categories"],
        summary="Update a product category", path_model=ProductCategoryPath,
        request_model=ProductCategoryWriteFields, response_model=ProductCategoryResource,
    )
    def update_category(self, category_id, data):
        return ProductCategoryService(request.env).update(category_id, data)

    @api_route(
        "/api/v1/product-categories/<string:category_id>", methods=["DELETE"],
        operation_id="deleteProductCategory", tags=["Product Categories"],
        summary="Delete an empty product category", path_model=ProductCategoryPath,
        response_model=ProductCategoryDeleteResponse,
    )
    def delete_category(self, category_id):
        return ProductCategoryService(request.env).delete(category_id)
