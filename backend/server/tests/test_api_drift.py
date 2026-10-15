"""Gate: the committed web API schema must match the backend. Fix with `just gen-api`."""

from __future__ import annotations

import json
from pathlib import Path

from agentos.main import create_app

COMMITTED = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "openapi.json"


def test_web_openapi_schema_is_up_to_date() -> None:
    current = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    committed = COMMITTED.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert current == committed, "API contract changed: run `just gen-api` and commit web/src/api/*"


def test_schema_includes_models_no_json_route_returns() -> None:
    """The error envelope reaches the web types only through the schema."""
    schemas = create_app().openapi()["components"]["schemas"]
    assert "ErrorBody" in schemas
