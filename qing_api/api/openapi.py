from collections import OrderedDict

from .registry import API_REGISTRY


ERROR_DESCRIPTIONS = {
    400: "Invalid request",
    401: "Authentication required",
    403: "Access denied",
    404: "Resource not found",
    409: "Request conflict",
    413: "Request body too large",
    415: "Unsupported media type",
    422: "Validation failed",
    429: "Rate limit exceeded",
    500: "Unexpected server error",
    503: "Service unavailable",
}


class ComponentRegistry:
    def __init__(self):
        self.schemas = OrderedDict()
        self.schema_classes = {}

    def add(self, schema_class):
        name = schema_class.__name__
        if name in self.schemas:
            if self.schema_classes[name] is not schema_class:
                raise ValueError("Duplicate API schema name: %s" % name)
            return
        self.schema_classes[name] = schema_class
        self.schemas[name] = {}
        self.schemas[name] = schema_class.to_openapi(self)


def build_openapi(installed_modules=None):
    components = ComponentRegistry()
    paths = OrderedDict()
    tags = OrderedDict()
    security_schemes = OrderedDict([
        ("bearerAuth", {
            "type": "http",
            "scheme": "bearer",
            "description": "Odoo API key with QingERP API scope.",
        }),
    ])
    for endpoint in API_REGISTRY.endpoints(installed_modules=installed_modules):
        if endpoint.security_scheme:
            definition = {
                "type": "http",
                "scheme": "bearer",
                "description": endpoint.security_description or "Bearer credential.",
            }
            existing = security_schemes.get(endpoint.security_scheme)
            if existing and existing != definition:
                raise ValueError("Conflicting API security scheme: %s" % endpoint.security_scheme)
            security_schemes[endpoint.security_scheme] = definition
        tags.update((tag, {"name": tag}) for tag in endpoint.tags)
        components.add(endpoint.response_model)
        if endpoint.path_model:
            components.add(endpoint.path_model)
        if endpoint.query_model:
            components.add(endpoint.query_model)
        if endpoint.header_model:
            components.add(endpoint.header_model)
        if endpoint.request_model:
            components.add(endpoint.request_model)
        path_item = paths.setdefault(endpoint.openapi_path, OrderedDict())
        for method in endpoint.methods:
            path_item[method.lower()] = _build_operation(endpoint, components)

    schemas = OrderedDict(components.schemas)
    schemas["ApiErrorDetail"] = _error_detail_schema()
    schemas["ApiErrorResponse"] = _error_response_schema()
    return OrderedDict([
        ("openapi", "3.0.3"),
        ("info", {
            "title": "QingERP API",
            "version": "1.0.0",
            "description": "Stable business API for QingERP integrations.",
        }),
        ("servers", [{"url": "/"}]),
        ("tags", list(tags.values())),
        ("paths", paths),
        ("components", {
            "securitySchemes": security_schemes,
            "schemas": schemas,
        }),
    ])


def _build_operation(endpoint, components):
    operation = OrderedDict([
        ("operationId", endpoint.operation_id),
        ("tags", list(endpoint.tags)),
        ("summary", endpoint.summary),
    ])
    if endpoint.description:
        operation["description"] = endpoint.description
    if endpoint.deprecated:
        operation["deprecated"] = True
    operation["parameters"] = _build_parameters(endpoint, components)
    if endpoint.request_model:
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/%s" % endpoint.request_model.__name__},
                },
            },
        }
    operation["responses"] = _build_responses(endpoint)
    if endpoint.security_scheme:
        operation["security"] = [{endpoint.security_scheme: []}]
    elif endpoint.auth == "bearer":
        operation["security"] = [{"bearerAuth": []}]
    return operation


def _build_parameters(endpoint, components):
    parameters = []
    for model, location in (
        (endpoint.path_model, "path"),
        (endpoint.query_model, "query"),
        (endpoint.header_model, "header"),
    ):
        if not model:
            continue
        for name, field in model._declared_fields.items():
            parameter_name = name.replace("_", "-") if location == "header" else name
            parameter = {
                "name": parameter_name,
                "in": location,
                "required": True if location == "path" else field.required,
                "schema": field.to_openapi(components),
            }
            if field.description:
                parameter["description"] = field.description
            parameters.append(parameter)
    return parameters


def _build_responses(endpoint):
    responses = OrderedDict()
    responses[str(endpoint.response_status)] = {
        "description": "Successful response",
        "headers": {
            "X-Request-Id": {
                "description": "Correlation ID for this request.",
                "schema": {"type": "string"},
            },
        },
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/%s" % endpoint.response_model.__name__},
            },
        },
    }
    for status in endpoint.errors:
        responses[str(status)] = {
            "description": ERROR_DESCRIPTIONS.get(status, "Error response"),
            "headers": {
                "X-Request-Id": {
                    "description": "Correlation ID for this request.",
                    "schema": {"type": "string"},
                },
            },
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
                },
            },
        }
    return responses


def _error_detail_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "message"],
        "properties": {
            "field": {"type": "string"},
            "code": {"type": "string"},
            "message": {"type": "string"},
        },
    }


def _error_response_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message", "request_id"],
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "request_id": {"type": "string"},
                    "details": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ApiErrorDetail"},
                    },
                },
            },
        },
    }
