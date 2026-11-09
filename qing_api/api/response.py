from dataclasses import dataclass, field

from odoo.http import request


@dataclass
class ApiResponse:
    data: object
    status: int = 200
    headers: dict = field(default_factory=dict)


def make_json_response(payload, *, request_id, status=200, headers=None):
    response_headers = dict(headers or {})
    response_headers["Cache-Control"] = "no-store"
    response_headers["X-Request-Id"] = request_id
    return request.make_json_response(payload, headers=response_headers, status=status)


def make_error_response(error, *, request_id):
    payload = {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": request_id,
        },
    }
    if error.details:
        payload["error"]["details"] = error.details
    return make_json_response(payload, request_id=request_id, status=error.status)
