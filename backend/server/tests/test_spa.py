"""spa.py: the built SPA served next to the API (AGENTOS_WEB_DIR, the container)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from agentos.config import Settings
from agentos.main import create_app
from agentos.spa import CACHE_IMMUTABLE, CACHE_REVALIDATE, _file_under
from tests.conftest import BASE_URL, CSRF
from tests.run_support import FakeProvider

INDEX = "<!doctype html><title>AgentOS</title>"
SCRIPT = "console.log('app')"


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text(SCRIPT, encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside web_dir", encoding="utf-8")
    return root


def serve(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, providers={"local": FakeProvider()}, run_loops=False)
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def spa(settings: Settings, web_dir: Path) -> Iterator[TestClient]:
    yield from serve(settings.model_copy(update={"web_dir": web_dir}))


@pytest.fixture
def api_only(settings: Settings) -> Iterator[TestClient]:
    assert settings.web_dir is None
    yield from serve(settings)


def is_index(response: Response) -> bool:
    return response.status_code == 200 and response.text == INDEX


@pytest.mark.parametrize("path", ["/", "/tickets/AOS-1", "/settings/users", "/queue?q=x"])
def test_client_routes_get_index_html(spa: TestClient, path: str) -> None:
    response = spa.get(path)
    assert is_index(response)
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == CACHE_REVALIDATE


def test_hashed_assets_are_cached_long_other_files_revalidate(spa: TestClient) -> None:
    asset = spa.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert asset.text == SCRIPT
    assert asset.headers["cache-control"] == CACHE_IMMUTABLE
    icon = spa.get("/favicon.svg")
    assert icon.status_code == 200
    assert icon.headers["cache-control"] == CACHE_REVALIDATE


def test_head_is_served(spa: TestClient) -> None:
    response = spa.head("/tickets/AOS-1")
    assert response.status_code == 200
    assert response.text == ""


def test_a_missing_asset_is_a_404_not_index_html(spa: TestClient) -> None:
    response = spa.get("/assets/index-old.js")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("path", ["/..%2fsecret.txt", "/assets/..%2f..%2fsecret.txt"])
def test_no_path_traversal(spa: TestClient, path: str) -> None:
    response = spa.get(path)
    assert "outside web_dir" not in response.text


def test_file_under_rejects_escapes(web_dir: Path) -> None:
    root = web_dir.resolve()
    assert _file_under(root, "index.html") == root / "index.html"
    assert _file_under(root, "../secret.txt") is None
    assert _file_under(root, str(web_dir.parent / "secret.txt")) is None  # absolute path
    assert _file_under(root, "assets") is None  # a directory is not a file
    assert _file_under(root, "bad\x00name") is None


@pytest.mark.parametrize("client_name", ["spa", "api_only"])
def test_api_paths_are_never_shadowed(client_name: str, request: pytest.FixtureRequest) -> None:
    client: TestClient = request.getfixturevalue(client_name)
    assert client.get("/api/health").json() == {"status": "ok"}
    for response in (client.get("/api/nope"), client.get("/api"), client.get("/api/")):
        assert response.status_code == 404
        assert response.json() == {
            "error": {"code": "not_found", "message": "Not Found", "issues": []}
        }
    # An unknown API path stays a 404 for every method; a known path with the wrong method a 405.
    assert client.post("/api/nope", headers=CSRF).status_code == 404
    assert client.delete("/api/health", headers=CSRF).status_code == 405


def test_without_web_dir_nothing_is_served(api_only: TestClient) -> None:
    for path in ("/", "/tickets/AOS-1", "/index.html"):
        response = api_only.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_the_spa_route_is_not_in_the_openapi_schema(spa: TestClient) -> None:
    paths = spa.get("/openapi.json").json()["paths"]
    assert all(p.startswith("/api") for p in paths)
