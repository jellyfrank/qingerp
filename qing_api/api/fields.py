import datetime
import re
import uuid
from decimal import Decimal, InvalidOperation


MISSING = object()


class Field:
    openapi_type = None
    openapi_format = None

    def __init__(
        self,
        *,
        default=MISSING,
        description=None,
        example=MISSING,
        nullable=False,
        read_only=False,
        required=False,
        sensitive=False,
        write_only=False,
    ):
        self.default = default
        self.description = description
        self.example = example
        self.name = None
        self.nullable = nullable
        self.read_only = read_only
        self.required = required
        self.sensitive = sensitive
        self.write_only = write_only

    def bind(self, name):
        self.name = name
        return self

    def deserialize(self, value, path):
        if value is None:
            if self.nullable:
                return None
            self._fail(path, "NULL_NOT_ALLOWED", "Null is not allowed")
        return value

    def serialize(self, value, path):
        return self.deserialize(value, path)

    def to_openapi(self, components):
        schema = {"type": self.openapi_type}
        if self.openapi_format:
            schema["format"] = self.openapi_format
        if self.description:
            schema["description"] = self.description
        if self.example is not MISSING:
            schema["example"] = self.example
        if self.default is not MISSING:
            schema["default"] = self.default
        if self.nullable:
            schema["nullable"] = True
        if self.read_only:
            schema["readOnly"] = True
        if self.write_only:
            schema["writeOnly"] = True
        return schema

    @staticmethod
    def _fail(path, code, message):
        raise ValueError({"code": code, "field": path, "message": message})


class String(Field):
    openapi_type = "string"

    def __init__(self, *, min_length=None, max_length=None, pattern=None, **kwargs):
        super().__init__(**kwargs)
        self.max_length = max_length
        self.min_length = min_length
        self.pattern = pattern

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        if not isinstance(value, str):
            self._fail(path, "TYPE", "Must be a string")
        if self.min_length is not None and len(value) < self.min_length:
            self._fail(path, "MIN_LENGTH", "Value is too short")
        if self.max_length is not None and len(value) > self.max_length:
            self._fail(path, "MAX_LENGTH", "Value is too long")
        if self.pattern and not re.search(self.pattern, value):
            self._fail(path, "PATTERN", "Value has an invalid format")
        return value

    def to_openapi(self, components):
        schema = super().to_openapi(components)
        if self.min_length is not None:
            schema["minLength"] = self.min_length
        if self.max_length is not None:
            schema["maxLength"] = self.max_length
        if self.pattern:
            schema["pattern"] = self.pattern
        return schema


class Boolean(Field):
    openapi_type = "boolean"

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "1"}:
            return True
        if isinstance(value, str) and value.lower() in {"false", "0"}:
            return False
        self._fail(path, "TYPE", "Must be a boolean")


class Number(Field):
    openapi_type = "number"

    def __init__(self, *, minimum=None, maximum=None, **kwargs):
        super().__init__(**kwargs)
        self.maximum = maximum
        self.minimum = minimum

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        if isinstance(value, bool):
            self._fail(path, "TYPE", "Must be a number")
        try:
            value = float(value)
        except (TypeError, ValueError):
            self._fail(path, "TYPE", "Must be a number")
        self._check_range(value, path)
        return value

    def _check_range(self, value, path):
        if self.minimum is not None and value < self.minimum:
            self._fail(path, "MINIMUM", "Value is below the minimum")
        if self.maximum is not None and value > self.maximum:
            self._fail(path, "MAXIMUM", "Value is above the maximum")

    def to_openapi(self, components):
        schema = super().to_openapi(components)
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        return schema


class Integer(Number):
    openapi_type = "integer"

    def deserialize(self, value, path):
        if value is None:
            return super().deserialize(value, path)
        if isinstance(value, bool):
            self._fail(path, "TYPE", "Must be an integer")
        try:
            converted = int(value)
        except (TypeError, ValueError):
            self._fail(path, "TYPE", "Must be an integer")
        if isinstance(value, float) and not value.is_integer():
            self._fail(path, "TYPE", "Must be an integer")
        if isinstance(value, str) and str(converted) != value.strip():
            self._fail(path, "TYPE", "Must be an integer")
        self._check_range(converted, path)
        return converted


class DecimalString(String):
    DECIMAL_PATTERN = r"^-?[0-9]+(?:\.[0-9]+)?$"

    def __init__(self, **kwargs):
        kwargs.setdefault("pattern", self.DECIMAL_PATTERN)
        super().__init__(**kwargs)

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        try:
            decimal_value = Decimal(value)
        except InvalidOperation:
            self._fail(path, "TYPE", "Must be a decimal string")
        if not decimal_value.is_finite():
            self._fail(path, "TYPE", "Must be a finite decimal string")
        return value


class Date(String):
    openapi_format = "date"

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            self._fail(path, "FORMAT", "Must be an ISO 8601 date")
        return value


class DateTime(String):
    openapi_format = "date-time"

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.datetime.fromisoformat(candidate)
        except ValueError:
            self._fail(path, "FORMAT", "Must be an RFC 3339 datetime")
        if parsed.tzinfo is None:
            self._fail(path, "TIMEZONE", "Datetime must include a timezone")
        return value


class UUID(String):
    openapi_format = "uuid"

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        try:
            uuid.UUID(value)
        except (AttributeError, ValueError):
            self._fail(path, "FORMAT", "Must be a UUID")
        return value


class Enum(String):
    def __init__(self, values, **kwargs):
        super().__init__(**kwargs)
        self.values = tuple(values)

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is not None and value not in self.values:
            self._fail(path, "ENUM", "Value is not an allowed option")
        return value

    def to_openapi(self, components):
        schema = super().to_openapi(components)
        schema["enum"] = list(self.values)
        return schema


class Object(Field):
    def __init__(self, schema, **kwargs):
        super().__init__(**kwargs)
        self.schema = schema

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        return self.schema.load(value, path=path)

    def serialize(self, value, path):
        if value is None and self.nullable:
            return None
        return self.schema.dump(value, path=path)

    def to_openapi(self, components):
        components.add(self.schema)
        schema = {"$ref": "#/components/schemas/%s" % self.schema.__name__}
        if self.description:
            schema["description"] = self.description
        if self.nullable:
            schema["nullable"] = True
        return schema


class List(Field):
    openapi_type = "array"

    def __init__(self, item, *, min_items=None, max_items=None, **kwargs):
        super().__init__(**kwargs)
        self.item = item
        self.max_items = max_items
        self.min_items = min_items

    def deserialize(self, value, path):
        value = super().deserialize(value, path)
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            self._fail(path, "TYPE", "Must be an array")
        if self.min_items is not None and len(value) < self.min_items:
            self._fail(path, "MIN_ITEMS", "Array contains too few items")
        if self.max_items is not None and len(value) > self.max_items:
            self._fail(path, "MAX_ITEMS", "Array contains too many items")
        return [self.item.deserialize(item, "%s[%s]" % (path, index)) for index, item in enumerate(value)]

    def serialize(self, value, path):
        if value is None and self.nullable:
            return None
        if not isinstance(value, (list, tuple)):
            self._fail(path, "TYPE", "Must be an array")
        return [self.item.serialize(item, "%s[%s]" % (path, index)) for index, item in enumerate(value)]

    def to_openapi(self, components):
        schema = super().to_openapi(components)
        schema["items"] = self.item.to_openapi(components)
        if self.min_items is not None:
            schema["minItems"] = self.min_items
        if self.max_items is not None:
            schema["maxItems"] = self.max_items
        return schema
