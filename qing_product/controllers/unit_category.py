from odoo import http
from odoo.http import request

from odoo.addons.qing_api.api import ApiResponse, ApiSchema, Boolean, String, api_route

from ..services.unit_category import UnitCategoryService


class UnitCategoryPath(ApiSchema):
    category_id = String(required=True, pattern=r"^uc_[0-9a-f]{32}$")


class UnitCategoryWriteFields(ApiSchema):
    name = String(min_length=1, max_length=256)


class UnitCategoryCreateRequest(UnitCategoryWriteFields):
    name = String(required=True, min_length=1, max_length=256)


class UnitCategoryResource(ApiSchema):
    id = String(required=True)
    name = String(required=True)
    reference_unit_id = String(required=True)


class UnitCategoryDeleteResponse(ApiSchema):
    id = String(required=True)
    deleted = Boolean(required=True)


class UnitCategoryController(http.Controller):
    @api_route(
        "/api/v1/unit-categories", methods=["POST"],
        operation_id="createUnitCategory", tags=["Unit Categories"],
        summary="Create a unit category",
        description="Creates a reference unit; Odoo 20 has no separate uom.category model.",
        request_model=UnitCategoryCreateRequest,
        response_model=UnitCategoryResource, response_status=201,
    )
    def create_category(self, data):
        result = UnitCategoryService(request.env).create(data)
        return ApiResponse(result, 201, {"Location": "/api/v1/unit-categories/%s" % result["id"]})

    @api_route(
        "/api/v1/unit-categories/<string:category_id>", methods=["PATCH"],
        operation_id="updateUnitCategory", tags=["Unit Categories"],
        summary="Rename a unit category", path_model=UnitCategoryPath,
        request_model=UnitCategoryWriteFields, response_model=UnitCategoryResource,
    )
    def update_category(self, category_id, data):
        return UnitCategoryService(request.env).update(category_id, data)

    @api_route(
        "/api/v1/unit-categories/<string:category_id>", methods=["DELETE"],
        operation_id="deleteUnitCategory", tags=["Unit Categories"],
        summary="Delete an empty unit category", path_model=UnitCategoryPath,
        response_model=UnitCategoryDeleteResponse,
    )
    def delete_category(self, category_id):
        return UnitCategoryService(request.env).delete(category_id)
