"""The error envelope: a 422 lists every issue at its field; other errors list none."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from fastapi.testclient import TestClient

from agentos.models import ErrorBody, TicketDetail

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]


def error_of(response: httpx.Response) -> ErrorBody:
    return ErrorBody.model_validate(response.json())


def test_a_body_with_two_bad_fields_gets_one_422_listing_both(authed: TestClient) -> None:
    response = authed.post("/api/tickets", json={"title": "", "priority": "urgent"}, headers=CSRF)
    assert response.status_code == 422
    error = error_of(response).error
    assert error.code == "validation_error"
    assert [issue.loc for issue in error.issues] == [["title"], ["priority"]]
    assert error.message.startswith("body.title: ")  # the first issue, in the envelope's format


def test_a_validator_message_comes_without_the_pydantic_prefix(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    key = create_ticket().key
    response = authed.patch(f"/api/tickets/{key}", json={"title": None}, headers=CSRF)
    assert response.status_code == 422
    [issue] = error_of(response).error.issues
    assert issue.loc == []  # the body as a whole
    assert issue.message.startswith("cannot")


def test_a_message_only_422_gets_one_issue_for_the_whole_request(authed: TestClient) -> None:
    response = authed.post("/api/tickets", json={"title": "T", "repo": "nope"}, headers=CSRF)
    assert response.status_code == 422
    error = error_of(response).error
    assert error.code == "validation_error"
    assert [(issue.loc, issue.message) for issue in error.issues] == [([], error.message)]


def test_other_errors_list_no_issues(authed: TestClient) -> None:
    response = authed.get("/api/tickets/NOPE-1")
    assert response.status_code == 404
    assert error_of(response).error.issues == []
