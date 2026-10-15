"""ApiError: the one exception services raise. Pure (imports only models).

Translation to HTTP responses lives in `agentos.http_errors`, so services and pure modules can
raise ApiError without depending on the web layer.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentos.models import ErrorBody, ErrorCode, ValidationIssue


class ApiError(Exception):
    """`issues` lists every problem of a 422. A 422 raised without any is sent with one
    request-level issue carrying the message (see `http_errors.error_response`)."""

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        issues: Sequence[ValidationIssue] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code: ErrorCode = code
        self.message = message
        self.issues = list(issues)


def issue_text(loc: Sequence[str | int], message: str) -> str:
    """`a.0.b: message`, the envelope message of a validation error; just the message at the root."""
    where = ".".join(str(part) for part in loc)
    return f"{where}: {message}" if where else message


def validation_error(issues: Sequence[ValidationIssue]) -> ApiError:
    """A 422 `validation_error` listing every issue (at least one); its message is the first's."""
    first = issues[0]
    return ApiError(422, "validation_error", issue_text(first.loc, first.message), issues)


# Declared on every route so generated clients know the error shape.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "4XX": {"model": ErrorBody, "description": "Client error"},
    "5XX": {"model": ErrorBody, "description": "Server error"},
}
