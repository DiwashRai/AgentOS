"""Shared fixtures: isolated settings (tmp data + workspaces, dev auth), the app and clients.

One app and client serve the whole session; each test gets its own `ctx` on a copy of the
template db the session app's startup migrated and seeded."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from anyio.from_thread import BlockingPortal
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentos.clock import to_db
from agentos.config import Settings
from agentos.context import AppContext, build_context
from agentos.main import create_app
from agentos.models import Me, Role, RunStatus, StageRunStatus, TicketDetail
from tests.run_support import FakeLauncher, RunDriver

FIXTURES = Path(__file__).parent / "fixtures"
RUNTIMES_TOML = FIXTURES / "runtimes_test.toml"
CSRF = {"X-AgentOS": "1"}
# Dev auth only accepts a loopback Host header (TestClient's default is `testserver`).
BASE_URL = "http://127.0.0.1:8000"

# login(username, role=None) -> Me: sign the `client` in (dev auth), optionally with a role.
LoginFn = Callable[..., Me]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "e2e: real server + launcher + worker + git (deselect with -m 'not e2e')"
    )


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    """Print the random-order seed (pytest-randomly's header line is hidden by `-q`)."""
    del exitstatus
    seed = config.getoption("randomly_seed", None)
    if seed is not None:
        terminalreporter.write_line(f"random order: --randomly-seed={seed}")


def _strip_agentos_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith("AGENTOS_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests see only the settings they pass: drop AGENTOS_* from the developer's shell. The
    workers the e2e tests spawn inherit the env, so GITHUB_ACTIONS and GH_TOKEN go too: inside
    an Actions job they would switch those workers to GitHub API publication."""
    _strip_agentos_env(monkeypatch)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


def _test_settings(root: Path) -> Settings:
    # _env_file=None: a developer's backend/.env never leaks into tests.
    return Settings(
        _env_file=None,
        data_dir=root / "data",
        workspaces_dir=root / "workspaces",
        dev_auth=True,
        config=RUNTIMES_TOML,
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return _test_settings(tmp_path)


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@dataclass(frozen=True)
class SessionServer:
    """One started app per test session (per xdist worker) and the db its startup left behind."""

    app: FastAPI
    client: TestClient
    portal: BlockingPortal
    template_db: Path


@pytest.fixture(scope="session")
def session_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SessionServer]:
    """Start one app (the real lifespan: checks, migrations, seeding) and keep a copy of its
    migrated, seeded db as the template every test's `ctx` starts from.

    Session fixtures run before the autouse `_clean_env`, so this strips AGENTOS_* itself while
    the settings are read and the app starts."""
    root = tmp_path_factory.mktemp("session-app")
    with ExitStack() as stack:
        with pytest.MonkeyPatch.context() as monkeypatch:
            _strip_agentos_env(monkeypatch)
            settings = _test_settings(root)
            # No background loops: tests drive dispatcher.tick / Reaper.tick themselves.
            app = create_app(settings, launcher=FakeLauncher(), run_loops=False)
            client = stack.enter_context(TestClient(app, base_url=BASE_URL))

        # Every connection is closed after startup, so SQLite has checkpointed its WAL.
        assert not settings.db_path.with_name(settings.db_path.name + "-wal").exists()
        template_db = root / "template.db"
        shutil.copyfile(settings.db_path, template_db)

        portal = client.portal
        assert portal is not None
        yield SessionServer(app, client, portal, template_db)


@pytest.fixture
def app(session_server: SessionServer) -> FastAPI:
    """The session's app. Tests never modify it; a test that needs other startup config builds
    its own app with `create_app`."""
    return session_server.app


@pytest.fixture
def ctx(
    session_server: SessionServer, settings: Settings, launcher: FakeLauncher
) -> Iterator[AppContext]:
    """This test's context on the session app: a fresh copy of the template db, built from this
    test's `settings` and `launcher`, its broker on the app's loop."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(session_server.template_db, settings.db_path)
    context = build_context(settings, launcher=launcher)
    loop = session_server.portal.call(asyncio.get_running_loop)
    context.broker.bind(loop)

    app = session_server.app
    app.state.ctx = context
    yield context

    session_server.portal.call(context.broker.close)
    del app.state.ctx  # a request that outlives its test fails loudly
    assert not app.dependency_overrides, "tests must not modify the shared app"


@pytest.fixture
def client(session_server: SessionServer, ctx: AppContext) -> TestClient:
    """The session's client, serving this test's `ctx`, signed out."""
    del ctx
    session_server.client.cookies.clear()
    return session_server.client


@pytest.fixture
def set_role(ctx: AppContext) -> Callable[[int, Role], None]:
    """Change a user's role directly in the db (dev logins always make admins)."""

    def _set(user_id: int, role: Role) -> None:
        with ctx.db.tx() as conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

    return _set


@pytest.fixture
def login(client: TestClient, set_role: Callable[[int, Role], None]) -> LoginFn:
    """Sign `client` in as `username` (replacing its session cookie); `role` demotes afterwards."""

    def _login(username: str, role: Role | None = None) -> Me:
        response = client.post("/api/auth/login", json={"username": username}, headers=CSRF)
        assert response.status_code == 200, response.text
        user = Me.model_validate(response.json())
        if role is not None and role != user.role:
            set_role(user.id, role)
            user = user.model_copy(update={"role": role})
        return user

    return _login


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).status_code == 200
    return client


# create_ticket(title="...", **TicketCreate fields) -> TicketDetail, as the `client`'s user.
CreateTicket = Callable[..., TicketDetail]


@pytest.fixture
def create_ticket(client: TestClient) -> CreateTicket:
    """POST /api/tickets as whoever `client` is signed in as (use with `authed` or `login`)."""

    def _create(title: str = "A ticket", **fields: Any) -> TicketDetail:
        response = client.post("/api/tickets", json={"title": title, **fields}, headers=CSRF)
        assert response.status_code == 201, response.text
        return TicketDetail.model_validate(response.json())

    return _create


# add_run(key, status, [(stage_id, stage_status), ...], start=..., created_by=...) -> run id
AddRun = Callable[..., int]
T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
FINISHED: frozenset[RunStatus] = frozenset({"succeeded", "gated", "failed", "cancelled"})


@pytest.fixture
def add_run(ctx: AppContext) -> AddRun:
    """Insert a run + its stage_runs straight into the db (the `runs` fixture drives real runs).

    Stage i starts at start+4i minutes and ends 3 minutes later; `pending` stages never started.
    The run starts at `start` unless queued/dispatched, and finishes unless active.
    """

    def _add(
        key: str,
        status: RunStatus,
        stages: Sequence[tuple[str, StageRunStatus]],
        *,
        start: datetime = T0,
        created_by: int | None = None,
    ) -> int:
        def ts(minutes: float) -> str:
            return to_db(start + timedelta(minutes=minutes))

        with ctx.db.tx() as conn:
            ticket = conn.execute("SELECT id FROM tickets WHERE key = ?", (key,)).fetchone()
            assert ticket is not None, key
            cursor = conn.execute(
                "INSERT INTO runs(ticket_id, status, from_stage, stage_ids, time_budget_s, "
                "launcher, created_by, created_at, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, 3600, 'local', ?, ?, ?, ?)",
                (
                    ticket["id"],
                    status,
                    stages[0][0],
                    json.dumps([s for s, _ in stages]),
                    created_by,
                    ts(-1),
                    None if status in ("queued", "dispatched") else ts(0),
                    ts(4 * len(stages)) if status in FINISHED else None,
                ),
            )
            run_id = cursor.lastrowid
            assert run_id is not None
            for i, (stage_id, st) in enumerate(stages):
                began = st != "pending"
                conn.execute(
                    "INSERT INTO stage_runs(run_id, idx, stage_id, status, started_at, "
                    "finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        i,
                        stage_id,
                        st,
                        ts(4 * i) if began else None,
                        ts(4 * i + 3) if began and st not in ("running", "pending") else None,
                    ),
                )
        return run_id

    return _add


@pytest.fixture
def runs(client: TestClient, ctx: AppContext, launcher: FakeLauncher) -> RunDriver:
    """Dispatch through the API, tick the dispatcher, claim and act as the worker."""
    return RunDriver(client, ctx, launcher)
