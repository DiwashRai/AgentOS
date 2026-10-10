"""Shared fixtures: isolated settings (tmp data + workspaces, dev auth), the app and clients."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentos.clock import to_db
from agentos.config import Settings
from agentos.context import AppContext
from agentos.main import create_app
from agentos.models import Me, Role, RunStatus, StageRunStatus, TicketDetail

FIXTURES = Path(__file__).parent / "fixtures"
RUNTIMES_TOML = FIXTURES / "runtimes_test.toml"
CSRF = {"X-AgentOS": "1"}
# Dev auth only accepts a loopback Host header (TestClient's default is `testserver`).
BASE_URL = "http://127.0.0.1:8000"

# login(username, role=None) -> Me: sign the `client` in (dev auth), optionally with a role.
LoginFn = Callable[..., Me]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests see only the settings they pass: drop AGENTOS_* from the developer's shell."""
    for name in list(os.environ):
        if name.upper().startswith("AGENTOS_"):
            monkeypatch.delenv(name)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # _env_file=None: a developer's backend/.env never leaks into tests.
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspaces_dir=tmp_path / "workspaces",
        dev_auth=True,
        config=RUNTIMES_TOML,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # `with` runs the lifespan (checks, migrations, app.state.ctx)
    with TestClient(app, base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def ctx(app: FastAPI, client: TestClient) -> AppContext:
    """The running app's context (db, settings). Depends on `client`, which starts the app."""
    del client
    context = app.state.ctx
    assert isinstance(context, AppContext)
    return context


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
    """Insert a run + its stage_runs straight into the db (until the run service exists).

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
