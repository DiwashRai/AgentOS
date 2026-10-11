"""Resolve (request 15): close a ticket as completed (done) or abandoned (closed) with an optional
note; PATCH `state` clears it; abandoned tickets stay on the board as `done`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.models import Me, Person, TicketDetail, TicketSummary

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
AddRun = Callable[..., int]
LoginFn = Callable[..., Me]


def resolve(client: TestClient, key: str, **body: Any) -> Any:
    return client.post(f"/api/tickets/{key}/resolve", json=body, headers=CSRF)


def patch(client: TestClient, key: str, **body: Any) -> Any:
    return client.patch(f"/api/tickets/{key}", json=body, headers=CSRF)


def code(response: Any) -> str:
    value: str = response.json()["error"]["code"]
    return value


def board(client: TestClient, query: str = "") -> dict[str, TicketSummary]:
    response = client.get(f"/api/tickets{query}")
    assert response.status_code == 200, response.text
    return {t["key"]: TicketSummary.model_validate(t) for t in response.json()}


def detail(client: TestClient, key: str) -> TicketDetail:
    return TicketDetail.model_validate(client.get(f"/api/tickets/{key}").json())


def as_person(me: Me) -> Person:
    return Person(id=me.id, display_name=me.display_name, avatar_url=me.avatar_url)


def test_complete_sets_done_and_the_resolution(
    login: LoginFn, client: TestClient, create_ticket: CreateTicket
) -> None:
    me = login("dev")
    t = create_ticket("Ship it")
    assert t.resolution is None
    response = resolve(client, t.key, outcome="completed", note="  shipped in v2 \n")
    assert response.status_code == 200, response.text
    d = TicketDetail.model_validate(response.json())
    assert d.state == "done"
    assert d.attention == "done"
    assert d.resolution is not None
    assert (d.resolution.outcome, d.resolution.note) == ("completed", "shipped in v2")
    assert d.resolution.by == as_person(me)
    assert isinstance(d.resolution.at, datetime)
    assert d.resolution.at.tzinfo is not None
    assert d.updated_at == d.resolution.at
    assert detail(client, t.key) == d
    assert board(client)[t.key].resolution == d.resolution


def test_abandon_closes_but_stays_on_the_board_as_done(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    t = create_ticket("Dead end")
    hidden = create_ticket("Archived")
    assert patch(authed, hidden.key, state="closed").status_code == 200
    response = resolve(authed, t.key, outcome="abandoned", note="   ")
    assert response.status_code == 200, response.text
    d = TicketDetail.model_validate(response.json())
    assert d.state == "closed"
    assert d.resolution is not None
    assert (d.resolution.outcome, d.resolution.note) == ("abandoned", None)

    listed = board(authed)  # no include_closed: abandoned is listed, PATCH-closed is not
    assert t.key in listed
    assert hidden.key not in listed
    assert listed[t.key].attention == "done"
    assert listed[t.key].attention_since == d.resolution.at  # "Done recently" ordering
    assert set(board(authed, "?include_closed=true")) == {t.key, hidden.key}


def test_note_limit_and_outcome_validation(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    ok = resolve(authed, t.key, outcome="completed", note="n" * 2000)
    assert ok.status_code == 200, ok.text
    assert ok.json()["resolution"]["note"] == "n" * 2000
    for body in (
        {"outcome": "completed", "note": "n" * 2001},
        {"outcome": "done"},
        {"note": "x"},
        {"outcome": "completed", "extra": 1},
    ):
        response = resolve(authed, t.key, **body)
        assert response.status_code == 422, body
        assert code(response) == "validation_error"
    response = resolve(authed, "AOS-999", outcome="completed")
    assert (response.status_code, code(response)) == (404, "ticket_not_found")


def test_run_active_blocks_resolve(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    t = create_ticket()
    add_run(t.key, "running", [("plan", "running")])
    response = resolve(authed, t.key, outcome="abandoned")
    assert (response.status_code, code(response)) == (409, "run_active")
    after = detail(authed, t.key)
    assert (after.state, after.resolution) == ("open", None)


@pytest.mark.parametrize(
    ("role", "status", "error"),
    [
        ("pending", 403, "pending_approval"),
        ("viewer", 403, "forbidden"),
        ("writer", 200, None),
        ("admin", 200, None),
    ],
)
def test_who_can_resolve(
    login: LoginFn,
    client: TestClient,
    create_ticket: CreateTicket,
    role: str,
    status: int,
    error: str | None,
) -> None:
    login("dev")
    t = create_ticket()
    login("sam", role=role)
    response = resolve(client, t.key, outcome="completed")
    assert response.status_code == status, response.text
    if error is not None:
        assert code(response) == error


def test_patch_state_clears_the_resolution(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    assert resolve(authed, t.key, outcome="abandoned", note="why").status_code == 200
    # A non-state edit keeps it.
    assert patch(authed, t.key, title="Renamed").json()["resolution"]["note"] == "why"
    # Reopen clears it.
    reopened = patch(authed, t.key, state="open").json()
    assert (reopened["state"], reopened["resolution"]) == ("open", None)
    # A plain Mark done (PATCH state=done) leaves it null: the UI falls back to `state`.
    assert resolve(authed, t.key, outcome="abandoned").status_code == 200
    done = patch(authed, t.key, state="done").json()
    assert (done["state"], done["resolution"]) == ("done", None)


def test_resolving_again_replaces_the_resolution(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    t = create_ticket()
    assert resolve(authed, t.key, outcome="abandoned", note="first").status_code == 200
    d = TicketDetail.model_validate(resolve(authed, t.key, outcome="completed").json())
    assert d.state == "done"
    assert d.resolution is not None
    assert (d.resolution.outcome, d.resolution.note) == ("completed", None)


def test_a_resolved_ticket_cannot_be_dispatched(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    t = create_ticket()
    assert resolve(authed, t.key, outcome="completed").status_code == 200
    response = authed.post(f"/api/tickets/{t.key}/runs", json={}, headers=CSRF)
    assert response.status_code == 409, response.text
