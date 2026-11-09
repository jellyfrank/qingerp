import functools
import inspect
import logging
import re
import uuid

from psycopg2 import IntegrityError

from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from ..compatibility import get_bearer_route_options
from .exception import ApiError, SchemaValidationError
from .registry import API_REGISTRY, Endpoint
from .request import parse_body, parse_headers, parse_path, parse_query
from .response import ApiResponse, make_error_response, make_json_response


_logger = logging.getLogger(__name__)

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def api_route(
    path,
    *,
    methods,
    operation_id,
    tags,
    summary,
    description=None,
    path_model=None,
    query_model=None,
    header_model=None,
    request_model=None,
    response_model=None,
    response_status=200,
    errors=(400, 401, 403, 404, 409, 415, 422, 500),
    auth="bearer",
    deprecated=False,
    readonly=False,
    save_session=None,
    max_body_bytes=None,
    security_scheme=None,
    security_description=None,
):
    if save_session is not None and not isinstance(save_session, bool):
        raise ValueError("save_session must be a boolean or None")
    if max_body_bytes is not None and (
        isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int) or max_body_bytes < 1
    ):
        raise ValueError("max_body_bytes must be a positive integer")
    if security_scheme is not None and (
        auth == "bearer"
        or not isinstance(security_scheme, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", security_scheme)
    ):
        raise ValueError("security_scheme requires a non-bearer route and a valid OpenAPI name")
    if security_description is not None and (
        security_scheme is None or not isinstance(security_description, str)
    ):
        raise ValueError("security_description requires security_scheme and a string description")
    normalized_methods = tuple(method.upper() for method in methods)

    def decorator(function):
        endpoint = Endpoint(
            auth=auth,
            deprecated=deprecated,
            description=description,
            errors=tuple(errors),
            header_model=header_model,
            methods=normalized_methods,
            module_name=_get_module_name(function),
            max_body_bytes=max_body_bytes,
            operation_id=operation_id,
            path=path,
            path_model=path_model,
            query_model=query_model,
            request_model=request_model,
            response_model=response_model,
            response_status=response_status,
            security_scheme=security_scheme,
            security_description=security_description,
            summary=summary,
            tags=tuple(tags),
        )
        API_REGISTRY.register(endpoint)

        @functools.wraps(function)
        def wrapped(controller, *args, **route_params):
            request_id = _get_request_id()
            try:
                _ensure_bearer_header(endpoint)
                call_params = parse_path(endpoint, route_params)
                query_values = parse_query(endpoint)
                header_values = parse_headers(endpoint)
                body_values = parse_body(endpoint)
                if endpoint.query_model:
                    call_params["query"] = query_values
                if endpoint.header_model:
                    call_params["headers"] = header_values
                if endpoint.request_model:
                    call_params["data"] = body_values
                result = function(controller, *args, **call_params)
                api_response = result if isinstance(result, ApiResponse) else ApiResponse(result, endpoint.response_status)
                payload = endpoint.response_model.dump(api_response.data)
                return make_json_response(
                    payload,
                    request_id=request_id,
                    status=api_response.status,
                    headers=api_response.headers,
                )
            except ApiError as error:
                request.env.cr.rollback()
                if isinstance(error, SchemaValidationError) and error.message == "Response validation failed":
                    _logger.exception("Qing API response schema validation failed: %s", endpoint.operation_id)
                    error = ApiError("INTERNAL_ERROR", "An unexpected server error occurred", 500)
                return make_error_response(error, request_id=request_id)
            except AccessError:
                request.env.cr.rollback()
                return make_error_response(ApiError("ACCESS_DENIED", "Access denied", 403), request_id=request_id)
            except MissingError:
                request.env.cr.rollback()
                return make_error_response(ApiError("RESOURCE_NOT_FOUND", "Resource not found", 404), request_id=request_id)
            except ValidationError:
                request.env.cr.rollback()
                return make_error_response(ApiError("BUSINESS_VALIDATION_ERROR", "Business validation failed", 422), request_id=request_id)
            except UserError:
                request.env.cr.rollback()
                return make_error_response(ApiError("BUSINESS_ERROR", "Business operation failed", 422), request_id=request_id)
            except IntegrityError:
                request.env.cr.rollback()
                return make_error_response(ApiError("CONFLICT", "The request conflicts with existing data", 409), request_id=request_id)
            except Exception:
                request.env.cr.rollback()
                _logger.exception("Unhandled Qing API error: %s", endpoint.operation_id)
                return make_error_response(ApiError("INTERNAL_ERROR", "An unexpected server error occurred", 500), request_id=request_id)

        route_options = {
            "type": "http",
            "methods": list(normalized_methods),
            "csrf": False,
            "readonly": readonly,
        }
        if auth == "bearer":
            route_options.update(get_bearer_route_options())
        else:
            route_options["auth"] = auth
        if save_session is not None:
            route_options["save_session"] = save_session
        return http.route(path, **route_options)(wrapped)

    return decorator


def _get_module_name(function):
    module_parts = function.__module__.split(".")
    if module_parts[:2] == ["odoo", "addons"] and len(module_parts) > 2:
        return module_parts[2]
    return module_parts[0]


def _get_request_id():
    candidate = request.httprequest.headers.get("X-Request-Id", "")
    if candidate and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return "req_%s" % uuid.uuid4().hex


def _ensure_bearer_header(endpoint):
    if endpoint.auth != "bearer":
        return
    authorization = request.httprequest.headers.get("Authorization", "")
    if not re.fullmatch(r"Bearer\s+\S+", authorization, flags=re.IGNORECASE):
        raise ApiError("AUTHENTICATION_REQUIRED", "A Bearer API key is required", 401)
