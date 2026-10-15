"""Worker authentication: `Authorization: Bearer <token>` (a dispatch, worker or client token).

Only parses the header (401 if it is missing or malformed); the service looks the token up by
hash. Reading the header from the Request keeps it out of the OpenAPI schema (the generated web
client never calls worker routes). Runs before body validation, like the role dependencies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from agentos.errors import ApiError


def bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "unauthorized", "Missing Authorization: Bearer <token>")
    return token


RunToken = Annotated[str, Depends(bearer_token)]
ClientBearer = Annotated[str, Depends(bearer_token)]
