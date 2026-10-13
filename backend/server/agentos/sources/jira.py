"""A small Jira REST client (SPEC §11, §12): login checks, and `JiraSource`, the ticket source
that imports an issue by key or browse URL (request 11) and searches by JQL for intake (request 14).

An adapter: plain values in, dataclasses out, its own exceptions for failures (the caller maps
them to `ApiError`). No db, no services, no web framework.

Credentials are passed per call (a person's own for login, the service account's for import) and
are never stored, logged or put into an exception message. Exceptions are raised `from None`, so
no traceback carries the httpx request (which holds the `Authorization` header).
"""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from agentos.models import JiraField, JiraFieldKind, Priority, TicketType
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
# What an intake search reads (no description: Cloud's v3 search serves it as ADF).
SEARCH_FIELDS = ("summary", "issuetype", "priority", "status", "assignee", "updated")
MAX_ERROR_CHARS = 300


class JiraError(SourceError):
    """Base class. Messages name the path and status only, never credentials."""


class JiraUnauthorized(JiraError, SourceUnauthorized):
    """Jira rejected the credentials (401/403)."""


class JiraNotFound(JiraError, SourceNotFound):
    """The issue doesn't exist or the credentials can't see it (Jira answers 404 or 403)."""


class JiraUnavailable(JiraError, SourceUnavailable):
    """Network error, timeout, 5xx or an unexpected response."""


class JiraBadQuery(JiraError):
    """A search was rejected (400): the message is Jira's own (e.g. a JQL syntax error), which
    names the query, never credentials."""


@dataclass(frozen=True, slots=True)
class JiraSearchPage:
    """The first page of a search (raw issue JSON). `count` = how many issues match: exact when
    `complete` (this page holds them all), else Jira's approximate count (Cloud)."""

    issues: list[dict[str, Any]]
    count: int
    complete: bool


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

    def fields(self, creds: JiraCredentials) -> list[dict[str, Any]]:
        """`GET /rest/api/2/field` (Cloud and Data Center): every field the credentials can see,
        as raw JSON objects. 401/403 → JiraUnauthorized; anything else but a 200 JSON array →
        JiraUnavailable."""
        path = "/rest/api/2/field"
        response = self.get(creds, path)
        if response.status_code in (401, 403):
            raise JiraUnauthorized(f"GET {path}: {response.status_code}")
        if response.status_code != 200:
            raise JiraUnavailable(f"GET {path}: {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise JiraUnavailable(f"GET {path}: not JSON") from None
        if not isinstance(data, list):
            raise JiraUnavailable(f"GET {path}: not a JSON array")
        return [f for f in data if isinstance(f, dict)]

    def search(
        self, creds: JiraCredentials, jql: str, fields: tuple[str, ...], limit: int
    ) -> JiraSearchPage:
        """The first `limit` issues matching `jql` (raw JSON), in the JQL's order.

        Cloud: `POST /rest/api/3/search/jql` (`/rest/api/2/search` is removed there), which has
        no total: `count` is exact when Jira says it was the last page, else Jira's approximate
        count (`POST /rest/api/3/search/approximate-count`). Data Center: `POST
        /rest/api/2/search`, whose `total` is exact. Both are read-only.

        400 → JiraBadQuery (Jira's message, e.g. a JQL syntax error); 401/403 → JiraUnauthorized;
        anything else but 200 → JiraUnavailable. Cloud answers an unknown field, project or value
        with no issues instead of a 400, so an empty Cloud page asks `jql_errors` why and raises
        JiraBadQuery with its message.
        """
        body: dict[str, Any] = {"jql": jql, "maxResults": limit, "fields": list(fields)}
        if self.deployment == "cloud":
            path = "/rest/api/3/search/jql"
        else:
            path = "/rest/api/2/search"
            body["startAt"] = 0
        data = self._search_json(path, self.post(creds, path, body))
        raw = data.get("issues")
        if not isinstance(raw, list):
            raise JiraUnavailable(f"POST {path}: no issues in the response")
        issues = [issue for issue in raw if isinstance(issue, dict)]
        if self.deployment == "cloud":
            if not issues and (errors := self.jql_errors(creds, jql)):
                raise JiraBadQuery(errors)
            if data.get("isLast") is True:
                return JiraSearchPage(issues=issues, count=len(issues), complete=True)
            return JiraSearchPage(
                issues=issues,
                count=max(self.approximate_count(creds, jql), len(issues)),
                complete=False,
            )
        total = data.get("total")
        if not isinstance(total, int) or isinstance(total, bool):
            raise JiraUnavailable(f"POST {path}: no total in the response")
        total = max(total, len(issues))
        return JiraSearchPage(issues=issues, count=total, complete=total <= len(issues))

    def approximate_count(self, creds: JiraCredentials, jql: str) -> int:
        """Cloud only: `POST /rest/api/3/search/approximate-count` (may lag recent changes)."""
        path = "/rest/api/3/search/approximate-count"
        data = self._search_json(path, self.post(creds, path, {"jql": jql}))
        count = data.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise JiraUnavailable(f"POST {path}: no count in the response")
        return count

    def jql_errors(self, creds: JiraCredentials, jql: str) -> str | None:
        """Cloud only: Jira's errors for `jql` (`POST /rest/api/3/jql/parse?validation=strict`,
        read-only), joined and cut short, or None when it's valid. Best effort: a failed check
        is None, since the search it explains already succeeded."""
        path = "/rest/api/3/jql/parse"
        try:
            response = self.post(creds, path, {"queries": [jql]}, params={"validation": "strict"})
            data = self._json(path, response, method="POST")
        except JiraError:
            return None
        queries = data.get("queries")
        first = queries[0] if isinstance(queries, list) and queries else None
        errors = first.get("errors") if isinstance(first, dict) else None
        if not isinstance(errors, list):
            return None
        return _short([e for e in errors if isinstance(e, str) and e])

    # ------------------------------------------------------------------ plumbing

    def get(
        self, creds: JiraCredentials, path: str, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """One authenticated GET. Network errors and timeouts → JiraUnavailable; 5xx too.
        Other statuses are returned for the caller to interpret."""
        return self._request("GET", creds, path, params=params)

    def post(
        self,
        creds: JiraCredentials,
        path: str,
        body: dict[str, Any],
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One authenticated POST of a JSON body (Jira's read-only search endpoints use POST).
        Failures as for `get`."""
        return self._request("POST", creds, path, params=params, json=body)

    def _request(
        self,
        method: Literal["GET", "POST"],
        creds: JiraCredentials,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": creds.authorization(), "Accept": "application/json"}
        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout, follow_redirects=False
            ) as client:
                response = client.request(
                    method, self.base_url + path, params=params, json=json, headers=headers
                )
        except httpx.HTTPError as exc:
            raise JiraUnavailable(f"{method} {path}: {type(exc).__name__}") from None
        if response.status_code >= 500:
            raise JiraUnavailable(f"{method} {path}: {response.status_code}")
        return response

    @staticmethod
    def _search_json(path: str, response: httpx.Response) -> dict[str, Any]:
        """A search endpoint's JSON: 400 → JiraBadQuery, 401/403 → JiraUnauthorized."""
        status = response.status_code
        if status == 400:
            raise JiraBadQuery(_jira_messages(response) or "Jira rejected the query")
        if status in (401, 403):
            raise JiraUnauthorized(f"POST {path}: {status}")
        return JiraClient._json(path, response, method="POST")

    @staticmethod
    def _json(path: str, response: httpx.Response, method: str = "GET") -> dict[str, Any]:
        if response.status_code != 200:
            raise JiraUnavailable(f"{method} {path}: {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise JiraUnavailable(f"{method} {path}: not JSON") from None
        if not isinstance(data, dict):
            raise JiraUnavailable(f"{method} {path}: not a JSON object")
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

    def fetch(self, external_id: str, extra_fields: Sequence[str] = ()) -> SourceTicket:
        """The issue, mapped, plus `extra_fields` (Jira field ids, read in the same call) as
        `field_values` (see `field_values`). JiraNotFound / JiraUnauthorized / JiraUnavailable on
        failure."""
        wanted = ISSUE_FIELDS + tuple(
            f for f in dict.fromkeys(extra_fields) if f not in ISSUE_FIELDS
        )
        data = self.client.issue(self._creds, external_id, wanted)
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
            field_values={f: field_values(fields.get(f)) for f in extra_fields},
        )

    def read_fields(self, external_id: str, field_ids: Sequence[str]) -> dict[str, list[str]]:
        """Just these fields of the issue (the mapping preview), as `field_values`.
        JiraNotFound / JiraUnauthorized / JiraUnavailable on failure."""
        data = self.client.issue(self._creds, external_id, tuple(dict.fromkeys(field_ids)))
        fields = data.get("fields")
        if not isinstance(fields, dict):
            raise JiraUnavailable(f"issue {external_id}: no fields in the response")
        return {f: field_values(fields.get(f)) for f in field_ids}

    def jira_fields(self) -> list[JiraField]:
        """Every field the service account can see (the mapping's field picker), by name.
        JiraUnauthorized / JiraUnavailable on failure."""
        out: list[JiraField] = []
        for raw in self.client.fields(self._creds):
            fid, name = raw.get("id"), raw.get("name")
            if isinstance(fid, str) and fid and isinstance(name, str) and name:
                out.append(JiraField(id=fid, name=name, kind=_field_kind(raw.get("schema"))))
        out.sort(key=lambda f: (f.name.casefold(), f.id))
        return out

    def post_comment(self, external_id: str, body: str) -> str:
        raise NotImplementedError("Jira comment sync is not built yet (SPEC §11 M1)")

    def search(self, jql: str, limit: int) -> JiraSearch:
        """The first `limit` issues matching `jql`, mapped like an import (request 14).

        `ORDER BY updated DESC` is appended when the JQL has no ORDER BY of its own, so the
        window holds the newest-updated matches. Issues without a usable key or `updated` are
        skipped (and not counted). JiraBadQuery / JiraUnauthorized / JiraUnavailable on failure.
        """
        if not _ORDER_BY_RE.search(jql):
            jql = f"{jql} ORDER BY updated DESC"
        page = self.client.search(self._creds, jql, SEARCH_FIELDS, limit)
        found = [c for c in map(self._candidate, page.issues) if c is not None]
        skipped = len(page.issues) - len(found)
        return JiraSearch(
            issues=tuple(found), count=max(page.count - skipped, len(found)), complete=page.complete
        )

    def _candidate(self, data: dict[str, Any]) -> JiraCandidate | None:
        key = data.get("key")
        fields = data.get("fields")
        if not isinstance(key, str) or not _ISSUE_KEY_RE.fullmatch(key):
            return None
        if not isinstance(fields, dict):
            return None
        updated = _aware_datetime(fields.get("updated"))
        if updated is None:
            return None
        key = key.upper()
        status = fields.get("status")
        assignee = fields.get("assignee")
        return JiraCandidate(
            key=key,
            title=(_str_or_none(fields.get("summary")) or "").strip() or key,
            type=_TYPES.get(_name(fields.get("issuetype")), "task"),
            priority=_PRIORITIES.get(_name(fields.get("priority")), "normal"),
            status=(_str_or_none(status.get("name")) or "") if isinstance(status, dict) else "",
            assignee=(
                _str_or_none(assignee.get("displayName")) if isinstance(assignee, dict) else None
            ),
            updated_at=updated,
            url=self.browse_url(key),
        )


@dataclass(frozen=True, slots=True)
class JiraCandidate:
    """A search hit, mapped like an import (`type`, `priority`) plus what a list shows."""

    key: str
    title: str
    type: TicketType
    priority: Priority
    status: str  # the Jira status name ("" if Jira sent none)
    assignee: str | None  # the assignee's display name
    updated_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class JiraSearch:
    """`issues` in the JQL's order; `count` = how many issues match (exact when `complete`)."""

    issues: tuple[JiraCandidate, ...]
    count: int
    complete: bool


_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_FIELD_KINDS: dict[str, JiraFieldKind] = {
    "string": "string",
    "option": "option",
    "version": "version",
    "array": "array",
}


def field_values(raw: object) -> list[str]:
    """A Jira field's raw JSON as plain strings (request 22B): a string → itself; an object → its
    `name`, else its `value` (versions, components, select options); an array → each element so;
    null, empty or anything else → none. Values are trimmed; blanks are dropped."""
    items = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for item in items:
        text: object = item
        if isinstance(item, dict):
            text = item.get("name")
            if not isinstance(text, str) or not text.strip():
                text = item.get("value")
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


def _field_kind(schema: object) -> JiraFieldKind:
    """From a field's `schema.type` (`{"type": "array", "items": "version", ...}`)."""
    kind = schema.get("type") if isinstance(schema, dict) else None
    return _FIELD_KINDS.get(kind, "other") if isinstance(kind, str) else "other"


def _aware_datetime(value: object) -> datetime | None:
    """Jira's `2026-10-11T17:21:17.475+0100` → an aware datetime; anything else → None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _jira_messages(response: httpx.Response) -> str | None:
    """Jira's `errorMessages` (+ field `errors`) joined and cut short, or None."""
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    messages = data.get("errorMessages")
    parts = [m for m in messages if isinstance(m, str) and m] if isinstance(messages, list) else []
    errors = data.get("errors")
    if isinstance(errors, dict):
        parts.extend(f"{k}: {v}" for k, v in errors.items() if isinstance(v, str) and v)
    return _short(parts)


def _short(parts: list[str]) -> str | None:
    """Messages joined on one line and cut to MAX_ERROR_CHARS, or None if there are none."""
    text = " ".join(" ".join(parts).split())
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1].rstrip() + "…"
    return text or None


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
