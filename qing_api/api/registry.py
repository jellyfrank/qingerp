import re
from collections import OrderedDict
from dataclasses import dataclass


ROUTE_PARAMETER_PATTERN = re.compile(r"<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>")


@dataclass(frozen=True)
class Endpoint:
    auth: str
    deprecated: bool
    description: str | None
    errors: tuple
    header_model: object
    methods: tuple
    module_name: str
    max_body_bytes: int | None
    operation_id: str
    path: str
    path_model: object
    query_model: object
    request_model: object
    response_model: object
    response_status: int
    security_scheme: str | None
    security_description: str | None
    summary: str
    tags: tuple

    @property
    def openapi_path(self):
        return ROUTE_PARAMETER_PATTERN.sub(lambda match: "{%s}" % match.group("name"), self.path)

    @property
    def path_parameter_names(self):
        return tuple(match.group("name") for match in ROUTE_PARAMETER_PATTERN.finditer(self.path))


class ApiRegistry:
    def __init__(self):
        self._endpoints = OrderedDict()
        self._operation_ids = {}

    def register(self, endpoint):
        self._validate(endpoint)
        for method in endpoint.methods:
            key = (method, endpoint.openapi_path)
            existing = self._endpoints.get(key)
            if existing and existing != endpoint:
                raise ValueError("Duplicate API route: %s %s" % key)
            self._endpoints[key] = endpoint
        existing_operation = self._operation_ids.get(endpoint.operation_id)
        if existing_operation and existing_operation != endpoint:
            raise ValueError("Duplicate API operationId: %s" % endpoint.operation_id)
        self._operation_ids[endpoint.operation_id] = endpoint

    def endpoints(self, installed_modules=None):
        unique = OrderedDict()
        for endpoint in self._endpoints.values():
            if installed_modules is not None and endpoint.module_name not in installed_modules:
                continue
            unique[endpoint.operation_id] = endpoint
        return tuple(unique.values())

    @staticmethod
    def _validate(endpoint):
        if not endpoint.path.startswith("/api/"):
            raise ValueError("API routes must start with /api/")
        if not endpoint.methods:
            raise ValueError("API route requires at least one HTTP method")
        if len(endpoint.methods) != 1:
            raise ValueError("Each API operation must declare exactly one HTTP method")
        if any(method not in {"DELETE", "GET", "PATCH", "POST", "PUT"} for method in endpoint.methods):
            raise ValueError("Unsupported API HTTP method")
        if "GET" in endpoint.methods and endpoint.request_model:
            raise ValueError("GET API routes cannot declare a request body")
        if not endpoint.operation_id:
            raise ValueError("API route requires operation_id")
        path_names = set(endpoint.path_parameter_names)
        schema_names = set(endpoint.path_model._declared_fields) if endpoint.path_model else set()
        if path_names != schema_names:
            raise ValueError("Path parameters and path_model fields must match")
        if not endpoint.response_model:
            raise ValueError("API route requires response_model")


API_REGISTRY = ApiRegistry()
