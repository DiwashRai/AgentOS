"""A small Jira REST client (SPEC §11, §12): login checks, and `JiraSource`, the ticket source
that imports an issue by key or browse URL (request 11).

An adapter: plain values in, dataclasses out, its own exceptions for failures (the caller maps
them to `ApiError`). No db, no services, no web framework.

Credentials are passed per call (a person's own for login, the service account's for import) and
are never stored, logged or put into an exception message. Exceptions are raised `from None`, so
no traceback carries the httpx request (which holds the `Authorization` header).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from agentos.models import Priority, TicketType
from agentos.sources.base import (
    SourceError,
    SourceNotFound,
    SourceTicket,
    SourceUnauthorized,
    SourceUnavailable,
)

Deployment = Literal["cloud", "datacenter"]
TIMEOUT_S = 10.0
# What an import reads; Cloud and DC both serve `description` as a wiki-markup string (or null).
ISSUE_FIELDS = ("summary", "description", "issuetype", "priority", "labels")


class JiraError(SourceError):
    """Base class. Messages name the path and status only, never credentials."""


class JiraUnauthorized(JiraError, SourceUnauthorized):
    """Jira rejected the credentials (401/403)."""


class JiraNotFound(JiraError, SourceNotFound):
    """The issue doesn't exist or the credentials can't see it (Jira answers 404 or 403)."""


class JiraUnavailable(JiraError, SourceUnavailable):
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

    def issue(
        self, creds: JiraCredentials, key: str, fields: tuple[str, ...] = ISSUE_FIELDS
    ) -> dict[str, Any]:
        """`GET /rest/api/2/issue/{key}?fields=...` (the raw JSON). 401 → JiraUnauthorized;
        403/404 → JiraNotFound (Jira hides issues the credentials can't browse); anything else
        but 200 → JiraUnavailable."""
        path = f"/rest/api/2/issue/{_segment(key)}"
        response = self.get(creds, path, params={"fields": ",".join(fields)})
        if response.status_code == 401:
            raise JiraUnauthorized(f"GET {path}: 401")
        if response.status_code in (403, 404):
            raise JiraNotFound(f"GET {path}: {response.status_code}")
        return self._json(path, response)

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


# ------------------------------------------------------------------ the ticket source (import)

# A Jira issue key as people type it (project keys may hold `_`; the ticket key validator decides
# whether AgentOS can use it). Matched case-insensitively, stored upper-case.
_ISSUE_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+")

# Jira issue type / priority names (Cloud and DC defaults), lower-cased → AgentOS values.
# Anything unknown: type `task`, priority `normal`.
_TYPES: dict[str, TicketType] = {
    "bug": "bug",
    "story": "feature",
    "epic": "feature",
    "new feature": "feature",
    "improvement": "improvement",
}
_PRIORITIES: dict[str, Priority] = {
    "highest": "high",
    "high": "high",
    "blocker": "high",
    "critical": "high",
    "medium": "normal",
    "low": "low",
    "lowest": "low",
    "minor": "low",
    "trivial": "low",
}


class JiraSource:
    """`TicketSource` for Jira: `parse_ref` takes a key or a `{JIRA_URL}/browse/KEY` link (or
    `?selectedIssue=KEY` on the same site); `fetch` reads the issue with the service account."""

    name: Literal["jira"] = "jira"

    def __init__(self, client: JiraClient, creds: JiraCredentials) -> None:
        self.client = client
        self._creds = creds
        site = urlsplit(client.base_url)
        self._host = (site.hostname or "").lower()
        self._port = _port(site.scheme, site.port)
        self._base_path = site.path.rstrip("/")

    def browse_url(self, key: str) -> str:
        return f"{self.client.base_url}/browse/{key}"

    def parse_ref(self, ref: str) -> str | None:
        """The issue key (upper-case), or None if `ref` is neither a key nor a link to an issue on
        this Jira site."""
        ref = ref.strip()
        if _ISSUE_KEY_RE.fullmatch(ref):
            return ref.upper()
        try:
            parts = urlsplit(ref)
            port = _port(parts.scheme, parts.port)
        except ValueError:  # e.g. a bad port or an unbalanced IPv6 bracket
            return None
        same_site = (parts.hostname or "").lower() == self._host and port == self._port
        if parts.scheme.lower() not in ("http", "https") or not same_site:
            return None
        path = parts.path.rstrip("/")
        browse = f"{self._base_path}/browse/"
        if path.startswith(browse):
            candidate = path[len(browse) :]
            return candidate.upper() if _ISSUE_KEY_RE.fullmatch(candidate) else None
        if path == self._base_path or path.startswith(f"{self._base_path}/"):
            selected = parse_qs(parts.query).get("selectedIssue", [])
            if len(selected) == 1 and _ISSUE_KEY_RE.fullmatch(selected[0]):
                return selected[0].upper()
        return None

    def fetch(self, external_id: str) -> SourceTicket:
        """The issue, mapped. JiraNotFound / JiraUnauthorized / JiraUnavailable on failure."""
        data = self.client.issue(self._creds, external_id)
        key = data.get("key")
        fields = data.get("fields")
        if not isinstance(key, str) or not _ISSUE_KEY_RE.fullmatch(key):
            raise JiraUnavailable(f"issue {external_id}: no key in the response")
        if not isinstance(fields, dict):
            raise JiraUnavailable(f"issue {external_id}: no fields in the response")
        description = fields.get("description")
        labels = fields.get("labels")
        key = key.upper()
        return SourceTicket(
            external_id=key,
            key=key,
            project=key.rsplit("-", 1)[0],
            title=(_str_or_none(fields.get("summary")) or "").strip(),
            description=description if isinstance(description, str) else "",
            type=_TYPES.get(_name(fields.get("issuetype")), "task"),
            priority=_PRIORITIES.get(_name(fields.get("priority")), "normal"),
            labels=[x for x in labels if isinstance(x, str)] if isinstance(labels, list) else [],
            url=self.browse_url(key),
        )

    def post_comment(self, external_id: str, body: str) -> str:
        raise NotImplementedError("Jira comment sync is not built yet (SPEC §11 M1)")


def _name(value: object) -> str:
    """`{"name": "Bug", ...}` → "bug"; anything else → ""."""
    name = value.get("name") if isinstance(value, dict) else None
    return name.strip().lower() if isinstance(name, str) else ""


def _port(scheme: str, port: int | None) -> int | None:
    """The effective port (`https://x` and `https://x:443` are the same site)."""
    return port if port is not None else {"http": 80, "https": 443}.get(scheme.lower())


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _segment(value: str) -> str:
    """A path segment, percent-encoded (keys are validated upstream; this is belt and braces)."""
    return quote(value, safe="")
