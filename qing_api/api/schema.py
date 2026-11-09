from collections import OrderedDict

from .exception import SchemaValidationError
from .fields import Field, MISSING


class ApiSchemaMeta(type):
    def __new__(mcs, name, bases, namespace):
        declared_fields = OrderedDict()
        for base in bases:
            declared_fields.update(getattr(base, "_declared_fields", {}))
        for attribute_name, value in namespace.items():
            if isinstance(value, Field):
                declared_fields[attribute_name] = value.bind(attribute_name)
        namespace["_declared_fields"] = declared_fields
        return super().__new__(mcs, name, bases, namespace)


class ApiSchema(metaclass=ApiSchemaMeta):
    allow_unknown = False

    @classmethod
    def load(cls, data, path="$", *, partial=False):
        if not isinstance(data, dict):
            raise SchemaValidationError([{
                "code": "TYPE",
                "field": path,
                "message": "Must be an object",
            }])

        issues = []
        result = {}
        unknown = sorted(set(data) - set(cls._declared_fields))
        if unknown and not cls.allow_unknown:
            issues.extend({
                "code": "UNKNOWN_FIELD",
                "field": cls._join_path(path, name),
                "message": "Unknown field",
            } for name in unknown)

        for name, field in cls._declared_fields.items():
            field_path = cls._join_path(path, name)
            if name not in data:
                if field.default is not MISSING:
                    result[name] = field.default() if callable(field.default) else field.default
                elif field.required and not partial and not field.read_only:
                    issues.append({
                        "code": "REQUIRED",
                        "field": field_path,
                        "message": "Field is required",
                    })
                continue
            if field.read_only:
                issues.append({
                    "code": "READ_ONLY",
                    "field": field_path,
                    "message": "Field is read-only",
                })
                continue
            try:
                result[name] = field.deserialize(data[name], field_path)
            except ValueError as error:
                issues.append(error.args[0])

        if issues:
            raise SchemaValidationError(issues)
        return result

    @classmethod
    def dump(cls, data, path="$."):
        issues = []
        result = {}
        for name, field in cls._declared_fields.items():
            if field.write_only:
                continue
            present, value = cls._get_value(data, name)
            field_path = cls._join_path(path.rstrip("."), name)
            if not present:
                if field.required:
                    issues.append({
                        "code": "REQUIRED",
                        "field": field_path,
                        "message": "Response field is required",
                    })
                continue
            try:
                result[name] = field.serialize(value, field_path)
            except (SchemaValidationError, ValueError) as error:
                if isinstance(error, SchemaValidationError):
                    issues.extend(error.details)
                else:
                    issues.append(error.args[0])
        if issues:
            raise SchemaValidationError(issues, message="Response validation failed")
        return result

    @classmethod
    def to_openapi(cls, components):
        properties = OrderedDict()
        required = []
        for name, field in cls._declared_fields.items():
            properties[name] = field.to_openapi(components)
            if field.required:
                required.append(name)
        schema = {
            "type": "object",
            "additionalProperties": cls.allow_unknown,
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _get_value(data, name):
        if isinstance(data, dict):
            return name in data, data.get(name)
        return hasattr(data, name), getattr(data, name, None)

    @staticmethod
    def _join_path(path, name):
        if path == "$":
            return "$.%s" % name
        return "%s.%s" % (path, name)
