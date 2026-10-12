"""ArtifactoryStore: a Store over a folder of an Artifactory generic repo (`design/mailbox.md`).

`base_url` is `https://host[/prefix]/artifactory/<repo>[/<folder>...]`; a store path `p` is the
file `{base_url}/{p}`. Files are written with PUT (plus `X-Checksum-Sha256`, which Artifactory
checks), read with GET, removed with DELETE (a folder is deleted recursively) and listed with the
storage API (`/artifactory/api/storage/<repo>/<folder>/<path>` -> `{"children": [{"uri": "/n"}]}`).

Basic auth. Network errors and 5xx are retried (3 attempts in all); any other answer but the
expected one raises StoreError. No credential ever appears in a raised message or a log line.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, TypeVar
from urllib.parse import urlsplit

import httpx

from agentos.stores.base import StoreError, StoreTooLarge

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_MESSAGE_CHARS = 300
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
_URL_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")

T = TypeVar("T")


class _Retryable(Exception):
    """A 5xx answer: worth another attempt."""


class ArtifactoryStore:
    # The waits between attempts: len + 1 attempts in all (tests shrink them).
    retry_delays: tuple[float, ...] = (0.5, 2.0)

    def __init__(
        self,
        *,
        base_url: str,
        user: str,
        password: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not user or not password:
            raise ValueError("an Artifactory user and password (or token) are required")
        self.base_url = base_url.rstrip("/")
        self._storage_url = _storage_url(self.base_url)
        self._user = user
        self._password = password
        self._transport = transport

    # ------------------------------------------------------------ the Store protocol

    def put(self, path: str, data: bytes) -> None:
        url = self._file_url(path)
        headers = {
            "X-Checksum-Sha256": hashlib.sha256(data).hexdigest(),
            "Content-Type": "application/octet-stream",
        }

        def call(client: httpx.Client) -> None:
            resp = client.put(url, content=data, headers=headers)
            if resp.status_code not in (200, 201):
                self._refuse("put", path, resp)

        self._attempts("put", path, call)

    def get(self, path: str, dest: Path, *, max_bytes: int) -> bool:
        url = self._file_url(path)

        def call(client: httpx.Client) -> bool:
            with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    return False
                if resp.status_code != 200:
                    resp.read()
                    self._refuse("get", path, resp)
                length = resp.headers.get("Content-Length", "")
                try:
                    if length.isdigit() and int(length) > max_bytes:
                        raise StoreTooLarge(f"{path} is over {max_bytes} bytes")
                    size = 0
                    with dest.open("wb") as out:
                        for chunk in resp.iter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise StoreTooLarge(f"{path} is over {max_bytes} bytes")
                            out.write(chunk)
                except BaseException:
                    dest.unlink(missing_ok=True)
                    raise
                return True

        return self._attempts("get", path, call)

    def delete(self, path: str) -> None:
        url = self._file_url(path)

        def call(client: httpx.Client) -> None:
            resp = client.delete(url)
            if resp.status_code not in (200, 202, 204, 404):
                self._refuse("delete", path, resp)

        self._attempts("delete", path, call)

    def list_dir(self, path: str) -> list[str]:
        url = f"{self._storage_url}/{_check_path(path)}"

        def call(client: httpx.Client) -> list[str]:
            resp = client.get(url)
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                self._refuse("list", path, resp)
            return _children(resp, path)

        return self._attempts("list", path, call)

    # ------------------------------------------------------------ plumbing

    def _file_url(self, path: str) -> str:
        return f"{self.base_url}/{_check_path(path)}"

    def _attempts(self, what: str, path: str, call: Callable[[httpx.Client], T]) -> T:
        """Run `call` up to len(retry_delays) + 1 times while the network fails or Artifactory
        answers 5xx. StoreError (4xx, too large) is raised at once."""
        delays = self.retry_delays
        problem = ""
        for attempt in range(len(delays) + 1):
            if attempt:
                log.warning("%s", self._safe(f"Artifactory {what} {path}: {problem}; retrying"))
                time.sleep(delays[attempt - 1])
            try:
                with httpx.Client(
                    transport=self._transport,
                    timeout=TIMEOUT,
                    auth=(self._user, self._password),
                    headers={"User-Agent": "AgentOS"},
                ) as client:
                    return call(client)
            except _Retryable as exc:
                problem = str(exc)
            except httpx.HTTPError as exc:
                problem = f"unreachable: {type(exc).__name__}: {exc}"
        # Raised outside the except blocks: no chained exception carrying the request's headers.
        raise StoreError(
            self._safe(
                f"Artifactory {what} {path} failed after {len(delays) + 1} attempts: {problem}"
            )
        )

    def _refuse(self, what: str, path: str, resp: httpx.Response) -> NoReturn:
        message = f"answered {resp.status_code}: {_artifactory_message(resp)}"
        if resp.status_code >= 500:
            raise _Retryable(message)
        raise StoreError(self._safe(f"Artifactory {what} {path} {message}"))

    def _safe(self, text: str) -> str:
        """Redact the credentials (and the Authorization value), then clip to one line."""
        basic = base64.b64encode(f"{self._user}:{self._password}".encode()).decode()
        for secret in (basic, self._password, self._user):
            text = text.replace(secret, "***")
        text = " ".join(text.split())
        return text if len(text) <= _MAX_MESSAGE_CHARS else text[: _MAX_MESSAGE_CHARS - 1] + "…"


def _storage_url(base_url: str) -> str:
    """The storage API URL of the folder `base_url` names. Raises ValueError if `base_url` isn't
    `https://host[/prefix]/artifactory/<repo>[/<folder>...]`."""
    parts = urlsplit(base_url)
    problem = None
    if parts.scheme != "https" or not parts.hostname:
        problem = "must be an https URL"
    elif parts.username is not None or parts.password is not None:
        problem = "must not hold credentials"
    elif parts.query or parts.fragment:
        problem = "must not have a query or fragment"
    if problem:
        # Not the URL itself: it might hold credentials.
        raise ValueError(f"Artifactory URL {problem}")
    segments = parts.path.split("/")[1:]
    if "artifactory" not in segments:
        raise ValueError("Artifactory URL must look like https://host/artifactory/<repo>/...")
    at = segments.index("artifactory")
    prefix, repo, folder = segments[:at], segments[at + 1 : at + 2], segments[at + 2 :]
    if not repo or repo[0] in ("", "api") or not all(_url_segment(s) for s in repo + folder):
        raise ValueError("Artifactory URL must look like https://host/artifactory/<repo>/...")
    head = "/".join(["", *prefix, "artifactory", "api", "storage", *repo, *folder])
    return f"https://{parts.netloc}{head}"


def _url_segment(segment: str) -> bool:
    return bool(_URL_SEGMENT.fullmatch(segment)) and segment not in (".", "..")


def _check_path(path: str) -> str:
    """`path` if it's a relative POSIX path of plain names; ValueError otherwise."""
    segments = path.split("/")
    if not path or not all(_SEGMENT.fullmatch(s) and s not in (".", "..") for s in segments):
        raise ValueError(f"bad store path {path!r}: relative names [A-Za-z0-9._-] only")
    return path


def _children(resp: httpx.Response, path: str) -> list[str]:
    try:
        data = resp.json()
    except ValueError:
        data = None
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list):
        raise StoreError(f"Artifactory list {path}: the answer has no children list")
    names: list[str] = []
    for child in children:
        uri = child.get("uri") if isinstance(child, dict) else None
        name = uri.strip("/") if isinstance(uri, str) else ""
        # A name a store path can't address is useless to the caller.
        if _SEGMENT.fullmatch(name) and name not in (".", ".."):
            names.append(name)
    return names


def _artifactory_message(resp: httpx.Response) -> str:
    """Artifactory's `errors[].message`, else the reason phrase."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    parts: list[str] = []
    if isinstance(data, dict):
        for err in data.get("errors") or []:
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                parts.append(err["message"])
    return "; ".join(parts) or resp.reason_phrase or "no message"
