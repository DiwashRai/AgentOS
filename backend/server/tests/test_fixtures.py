"""The shared session app keeps tests isolated: every test starts signed out on a pristine db.

Run twice; the first run dirties what the second asserts is clean, so a serial run catches a leak
whichever order the two run in."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from tests.conftest import CreateTicket, LoginFn


@pytest.mark.parametrize("attempt", [1, 2])
def test_each_test_starts_pristine(
    attempt: int,
    client: TestClient,
    ctx: AppContext,
    login: LoginFn,
    create_ticket: CreateTicket,
) -> None:
    del attempt
    assert not client.cookies
    assert client.get("/api/me").status_code == 401
    with ctx.db.read() as conn:
        counts = conn.execute(
            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM sessions), "
            "(SELECT COUNT(*) FROM tickets), (SELECT COUNT(*) FROM runs)"
        ).fetchone()
    assert tuple(counts) == (0, 0, 0, 0)

    login("leaky")
    assert create_ticket("Leak").key == "AOS-1"
