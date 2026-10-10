"""FastAPI dependencies shared by every feature's routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from agentos.context import AppContext


def get_ctx(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if not isinstance(ctx, AppContext):
        raise RuntimeError(
            "app.state.ctx is not set: the lifespan did not run (use `with TestClient`)"
        )
    return ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]
