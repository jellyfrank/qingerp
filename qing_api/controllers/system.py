from odoo import http
from odoo.http import request

from ..api import ApiSchema, Integer, String, api_route


class SystemPingResponse(ApiSchema):
    company_id = Integer(required=True)
    status = String(required=True)
    user_id = Integer(required=True)


class QingApiSystemController(http.Controller):
    @api_route(
        "/api/v1/system/ping",
        methods=["GET"],
        operation_id="pingSystem",
        tags=["System"],
        summary="Check API authentication and identity",
        response_model=SystemPingResponse,
        errors=(401, 403, 500),
        readonly=True,
    )
    def ping(self):
        return {
            "company_id": request.env.company.id,
            "status": "ok",
            "user_id": request.env.user.id,
        }
