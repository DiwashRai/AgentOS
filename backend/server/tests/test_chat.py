"""Ticket chat: posting and paging (SPEC §10)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from agentos.models import ChatMessage, Me, TicketDetail

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


def say(client: TestClient, key: str, body: str) -> Any:
    return client.post(f"/api/tickets/{key}/chat", json={"body": body}, headers=CSRF)


def bodies(client: TestClient, key: str, query: str = "") -> list[str]:
    response = client.get(f"/api/tickets/{key}/chat{query}")
    assert response.status_code == 200, response.text
    return [ChatMessage.model_validate(m).body for m in response.json()]


def test_post_and_list(login: LoginFn, client: TestClient, create_ticket: CreateTicket) -> None:
    login("dev")
    t = create_ticket()
    viewer = login("vic", "viewer")  # any signed-in user may chat
    response = say(client, t.key, "Hello there")
    assert response.status_code == 201, response.text
    msg = ChatMessage.model_validate(response.json())
    assert (msg.ticket_key, msg.body) == (t.key, "Hello there")
    assert msg.author.id == viewer.id
    assert msg.author.display_name == "vic"
    listed = client.get(f"/api/tickets/{t.key}/chat").json()
    assert [ChatMessage.model_validate(m) for m in listed] == [msg]
    assert client.get(f"/api/tickets/{t.key}").json()["chat_count"] == 1


def test_post_validates(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    for body in ("", "   \n", "x" * 4001):
        response = say(authed, t.key, body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "validation_error"
    assert say(authed, t.key, "x" * 4000).status_code == 201
    missing = say(authed, "AOS-404", "hi")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ticket_not_found"
    assert authed.get("/api/tickets/AOS-404/chat").status_code == 404


def test_paging(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    other = create_ticket()
    say(authed, other.key, "elsewhere")
    ids = [say(authed, t.key, f"m{i}").json()["id"] for i in range(1, 6)]
    assert bodies(authed, t.key) == ["m1", "m2", "m3", "m4", "m5"]  # oldest first
    assert bodies(authed, t.key, "?limit=2") == ["m4", "m5"]  # the latest page
    assert bodies(authed, t.key, f"?before_id={ids[3]}&limit=2") == ["m2", "m3"]
    assert bodies(authed, t.key, f"?before_id={ids[0]}") == []
    assert bodies(authed, t.key, f"?after_id={ids[1]}&limit=2") == ["m3", "m4"]
    assert bodies(authed, t.key, f"?after_id={ids[4]}") == []
    assert bodies(authed, other.key) == ["elsewhere"]
    assert authed.get(f"/api/tickets/{t.key}/chat?limit=0").status_code == 422
    counts = {s["key"]: s["chat_count"] for s in authed.get("/api/tickets").json()}
    assert counts == {t.key: 5, other.key: 1}
