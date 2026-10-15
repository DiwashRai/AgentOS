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


def validation_error(issues: Sequence[ValidationIssue]) -> ApiError:
    """A 422 `validation_error` listing every issue. Its message is the first issue as
    `a.0.b: message` (just the message when `loc` is empty); no issues give one request-level
    "Invalid." issue."""
    issues = list(issues) or [ValidationIssue(loc=[], message="Invalid.")]
    first = issues[0]
    where = ".".join(str(part) for part in first.loc)
    message = f"{where}: {first.message}" if where else first.message
    return ApiError(422, "validation_error", message, issues)


def field_error(loc: Sequence[str | int], message: str) -> ApiError:
    """A 422 `validation_error` with one issue at `loc`."""
    return validation_error([ValidationIssue(loc=list(loc), message=message)])


# Declared on every route so generated clients know the error shape.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "4XX": {"model": ErrorBody, "description": "Client error"},
    "5XX": {"model": ErrorBody, "description": "Server error"},
}
