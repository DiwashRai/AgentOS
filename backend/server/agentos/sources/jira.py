"""A small Jira REST client (SPEC §11, §12): login checks today, issue import next (request 11).

An adapter: plain values in, dataclasses out, its own exceptions for failures (the caller maps
them to `ApiError`). No db, no services, no web framework.

Credentials are passed per call (a person's own for login, the service account's for import) and
are never stored, logged or put into an exception message. Exceptions are raised `from None`, so
no traceback carries the httpx request (which holds the `Authorization` header).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote

import httpx

Deployment = Literal["cloud", "datacenter"]
TIMEOUT_S = 10.0


class JiraError(Exception):
    """Base class. Messages name the path and status only, never credentials."""


class JiraUnauthorized(JiraError):
    """Jira rejected the credentials (401/403)."""


class JiraUnavailable(JiraError):
    """Network error, timeout, 5xx or an unexpected response."""


@dataclass(frozen=True, slots=True)
class JiraCredentials:
    """Cloud: `email` + classic API token (HTTP Basic). Data Center: a PAT (Bearer), no email."""

    token: str = field(repr=False)
    email: str | None = None

    def authorization(self) -> str:
        if self.email is None:
            return f"Bearer {self.token}"
        raw = f"{self.email}:{self.token}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True, slots=True)
class JiraProfile:
    """`/rest/api/2/myself`. `subject` = `accountId` (Cloud) or `key` (Data Center)."""

    subject: str
    display_name: str
    email: str | None
    avatar_url: str | None


class JiraClient:
    """`base_url` is the site (`https://x.atlassian.net`, or a DC URL with its context path).

    `transport` lets tests inject an `httpx.MockTransport`; production uses the default.
    """

    def __init__(
        self,
        base_url: str,
        deployment: Deployment,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.deployment: Deployment = deployment
        self._transport = transport
        self._timeout = httpx.Timeout(timeout_s)

    def credentials(self, *, token: str, email: str | None) -> JiraCredentials:
        """The deployment's credential shape: Cloud uses the email, Data Center ignores it."""
        return JiraCredentials(token=token, email=email if self.deployment == "cloud" else None)

    # ------------------------------------------------------------------ endpoints

    def myself(self, creds: JiraCredentials) -> JiraProfile:
        """The credentials' owner. JiraUnauthorized on 401/403; JiraUnavailable otherwise."""
        path = "/rest/api/2/myself"
        response = self.get(creds, path)
        if response.status_code in (401, 403):
            raise JiraUnauthorized(f"GET {path}: {response.status_code}")
        data = self._json(path, response)
        id_field = "accountId" if self.deployment == "cloud" else "key"
        subject = data.get(id_field)
        if not isinstance(subject, str) or not subject:
            raise JiraUnavailable(f"GET {path}: no {id_field} in the response")
        email = _str_or_none(data.get("emailAddress"))
        avatars = data.get("avatarUrls")
        avatar = _str_or_none(avatars.get("48x48")) if isinstance(avatars, dict) else None
        return JiraProfile(
            subject=subject,
            display_name=_str_or_none(data.get("displayName")) or email or subject,
            email=email,
            avatar_url=avatar,
        )

    def can_browse_project(self, creds: JiraCredentials, key: str) -> bool:
        """`GET /rest/api/2/project/{key}`: 200 → True; 401/403/404 → False (unknown and hidden
        projects look the same); anything else → JiraUnavailable."""
        path = f"/rest/api/2/project/{_segment(key)}"
        status = self.get(creds, path).status_code
        if status == 200:
            return True
        if status in (401, 403, 404):
            return False
        raise JiraUnavailable(f"GET {path}: {status}")

    # ------------------------------------------------------------------ plumbing

    def get(
        self, creds: JiraCredentials, path: str, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """One authenticated GET. Network errors and timeouts → JiraUnavailable; 5xx too.
        Other statuses are returned for the caller to interpret."""
        headers = {"Authorization": creds.authorization(), "Accept": "application/json"}
        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout, follow_redirects=False
            ) as client:
                response = client.get(self.base_url + path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise JiraUnavailable(f"GET {path}: {type(exc).__name__}") from None
        if response.status_code >= 500:
            raise JiraUnavailable(f"GET {path}: {response.status_code}")
        return response

    @staticmethod
    def _json(path: str, response: httpx.Response) -> dict[str, Any]:
        if response.status_code != 200:
            raise JiraUnavailable(f"GET {path}: {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise JiraUnavailable(f"GET {path}: not JSON") from None
        if not isinstance(data, dict):
            raise JiraUnavailable(f"GET {path}: not a JSON object")
        return data


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _segment(value: str) -> str:
    """A path segment, percent-encoded (keys are validated upstream; this is belt and braces)."""
    return quote(value, safe="")
