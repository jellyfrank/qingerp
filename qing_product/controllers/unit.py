from odoo import http
from odoo.http import request

from odoo.addons.qing_api.api import (
    ApiResponse, ApiSchema, Boolean, DecimalString, String, api_route,
)

from ..services.unit import UnitService


class UnitPath(ApiSchema):
    unit_id = String(required=True, pattern=r"^uom_[0-9a-f]{32}$")


class UnitWriteFields(ApiSchema):
    name = String(min_length=1, max_length=256)
    category_id = String(pattern=r"^uc_[0-9a-f]{32}$")
    factor = DecimalString(max_length=32, description="Quantity in the category reference unit")


class UnitCreateRequest(UnitWriteFields):
    name = String(required=True, min_length=1, max_length=256)
    category_id = String(required=True, pattern=r"^uc_[0-9a-f]{32}$")
    factor = DecimalString(required=True, max_length=32)


class UnitResource(ApiSchema):
    id = String(required=True)
    name = String(required=True)
    category_id = String(required=True)
    factor = DecimalString(required=True)


class UnitDeleteResponse(ApiSchema):
    id = String(required=True)
    deleted = Boolean(required=True)


class UnitController(http.Controller):
    @api_route(
        "/api/v1/units", methods=["POST"], operation_id="createUnit",
        tags=["Units"], summary="Create a unit",
        request_model=UnitCreateRequest, response_model=UnitResource,
        response_status=201,
    )
    def create_unit(self, data):
        result = UnitService(request.env).create(data)
        return ApiResponse(result, 201, {"Location": "/api/v1/units/%s" % result["id"]})

    @api_route(
        "/api/v1/units/<string:unit_id>", methods=["PATCH"],
        operation_id="updateUnit", tags=["Units"], summary="Update a unit",
        path_model=UnitPath, request_model=UnitWriteFields, response_model=UnitResource,
    )
    def update_unit(self, unit_id, data):
        return UnitService(request.env).update(unit_id, data)

    @api_route(
        "/api/v1/units/<string:unit_id>", methods=["DELETE"],
        operation_id="deleteUnit", tags=["Units"], summary="Delete a non-reference unit",
        path_model=UnitPath, response_model=UnitDeleteResponse,
    )
    def delete_unit(self, unit_id):
        return UnitService(request.env).delete(unit_id)
