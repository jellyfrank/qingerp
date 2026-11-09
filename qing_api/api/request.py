from werkzeug.exceptions import BadRequest

from odoo.http import request

from .exception import ApiError, SchemaValidationError
from .fields import List


def parse_path(endpoint, route_params):
    if not endpoint.path_model:
        return {}
    values = {name: route_params.get(name) for name in endpoint.path_parameter_names}
    return endpoint.path_model.load(values, path="path")


def parse_query(endpoint):
    query_args = request.httprequest.args
    if not endpoint.query_model:
        if query_args:
            details = [{
                "code": "UNKNOWN_FIELD",
                "field": "query.%s" % name,
                "message": "Unknown query parameter",
            } for name in sorted(query_args)]
            raise SchemaValidationError(details)
        return None

    values = {}
    for name, field in endpoint.query_model._declared_fields.items():
        if name not in query_args:
            continue
        values[name] = query_args.getlist(name) if isinstance(field, List) else query_args.get(name)
    unknown = sorted(set(query_args) - set(endpoint.query_model._declared_fields))
    for name in unknown:
        values[name] = query_args.get(name)
    return endpoint.query_model.load(values, path="query")


def parse_headers(endpoint):
    if not endpoint.header_model:
        return None
    values = {}
    for name in endpoint.header_model._declared_fields:
        header_name = name.replace("_", "-")
        if header_name in request.httprequest.headers:
            values[name] = request.httprequest.headers[header_name]
    return endpoint.header_model.load(values, path="headers")


def parse_body(endpoint):
    if not endpoint.request_model:
        return None
    if request.httprequest.mimetype != "application/json":
        raise ApiError(
            code="UNSUPPORTED_MEDIA_TYPE",
            message="Content-Type must be application/json",
            status=415,
        )
    if endpoint.max_body_bytes is not None:
        content_length = request.httprequest.content_length
        if content_length is not None and content_length > endpoint.max_body_bytes:
            raise ApiError(
                code="REQUEST_TOO_LARGE",
                message="Request body exceeds the allowed size",
                status=413,
            )
        raw_body = request.httprequest.get_data(cache=True)
        if len(raw_body) > endpoint.max_body_bytes:
            raise ApiError(
                code="REQUEST_TOO_LARGE",
                message="Request body exceeds the allowed size",
                status=413,
            )
    try:
        payload = request.httprequest.get_json(force=False, silent=False)
    except BadRequest as error:
        raise ApiError(code="INVALID_JSON", message="Request body is not valid JSON", status=400) from error
    return endpoint.request_model.load(payload, path="body")
