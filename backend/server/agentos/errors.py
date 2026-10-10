"""ApiError: the one exception services raise. Pure (imports only models).

Translation to HTTP responses lives in `agentos.http_errors`, so services and pure modules can
raise ApiError without depending on the web layer.
"""

from __future__ import annotations

from agentos.models import ErrorBody, ErrorCode


class ApiError(Exception):
    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code: ErrorCode = code
        self.message = message


# Declared on every route so generated clients know the error shape.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "4XX": {"model": ErrorBody, "description": "Client error"},
    "5XX": {"model": ErrorBody, "description": "Server error"},
}
