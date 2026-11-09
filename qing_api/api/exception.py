class ApiError(Exception):
    """Stable API error carrying an HTTP status and public details."""

    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or []
        self.message = message
        self.status = status


class SchemaValidationError(ApiError):
    """Request data does not satisfy its declared schema."""

    def __init__(self, details, message="Request validation failed"):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status=422,
            details=details,
        )
