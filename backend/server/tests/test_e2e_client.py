"""End to end for client mode, no network and no LLM: the real server (uvicorn on a free loopback
port, dispatcher and reaper loops on) with the recording `FakeProvider` as its configured provider
(`github`; it starts nothing), and users' clients as real `worker.py --client` processes that start
real run workers. Agent stages run `fake_agent.py`; the remote is a temp bare repo, reached as in
`test_e2e.py` through `url.<file-url>.insteadOf` in the env file the client hands its run workers
(`--env-file`).

Each test signs in its own users, so their client queues and online states are independent.

Skip with `pytest -m "not e2e"`.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

import agentos.main
from agentos.clock import utcnow
from agentos.config import WORKER_PATH, Settings
from agentos.context import AppContext
from agentos.main import create_app
from agentos.models import ClientToken, ClientTokenCreated, Me, RunSummary, TaskProgress
from agentos.pipeline.runtime import NamedRuntime
from agentos.pipeline.space import RepoSpec, SpaceConfig
from agentos.runs import dispatcher
from agentos.runs.reaper import INTERVAL_S as REAPER_INTERVAL_S
from agentos.runs.reaper import Reaper
from agentos.worker_api.routes import LEASE_RETRY_S
from tests.run_support import FakeProvider
from tests.settings_support import seed_test_settings
from tests.test_e2e import (
    CSRF,
    DEADLINE_S,
    E2E,
    FAKE_AGENT,
    GIT_ENV,
    POLL_S,
    REPO_URL,
    TERMINAL,
    _free_socket,
    assert_clean_branch,
    make_remote,
    pid_alive,
    rail,
)

pytestmark = pytest.mark.e2e

WORKER_PID_RE = re.compile(r"\(pid (\d+)\)")


# ------------------------------------------------------------------ the server


@dataclass(frozen=True)
class Server:
    url: str
    root: Path
    ctx: AppContext
    remote: Path
    provider: FakeProvider
    env_file: Path  # the clients' --env-file


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    root = tmp_path_factory.mktemp("e2e-client")
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.upper().startswith("AGENTOS_"):
                mp.delenv(name)
        mp.setattr(dispatcher, "INTERVAL_S", 0.1)
        mp.setattr(agentos.main, "REAPER_INTERVAL_S", 0.5)  # the real reaper watches every run

        remote = make_remote(root)
        env_file = root / "client.env"
        env_file.write_bytes(
            (
                "GIT_CONFIG_COUNT=1\n"
                f"GIT_CONFIG_KEY_0='url.{remote.as_uri()}.insteadOf'\n"
                f"GIT_CONFIG_VALUE_0='{REPO_URL}'\n"
                "AGENTOS_PR_LOOKUP=0\n"  # never ask a real `gh` about the fake remote
            ).encode()
        )

        sock = _free_socket()
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        settings = Settings(
            _env_file=None,
            data_dir=root / "data",
            dev_auth=True,
            public_url=url,
            launcher="github",
            github_token=SecretStr("e2e-unused"),  # the fake provider never calls GitHub
        )
        seed_test_settings(
            settings,
            space=SpaceConfig(repos=(RepoSpec(name="remote", url=REPO_URL),)),
            runtimes=[
                NamedRuntime(
                    name="fake", argv=[sys.executable, str(FAKE_AGENT)], prompt_via="stdin"
                )
            ],
        )
        provider = FakeProvider()
        app = create_app(settings, providers={"github": provider})
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, name="e2e-client-uvicorn", daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            assert thread.is_alive(), "the server failed to start (see the captured log)"
            assert time.monotonic() < deadline, "the server did not start within 20 s"
            time.sleep(0.05)

        try:
            yield Server(url, root, app.state.ctx, remote, provider, env_file)
        finally:
            server.should_exit = True
            thread.join(30)
            sock.close()


# ------------------------------------------------------------------ users and their clients


@dataclass
class User(E2E):
    """A signed-in user (their own cookie session). Their client keeps its run workspaces and
    logs in `logs` = `<client_root>/runs`."""

    client_root: Path = field(kw_only=True)

    def dispatch(
        self, key: str, from_stage: str | None = None, target: str | None = None
    ) -> RunSummary:
        """POST /api/tickets/{key}/runs; `target=None` leaves it out (the server's route)."""
        body: dict[str, Any] = {} if from_stage is None else {"from_stage": from_stage}
        if target is not None:
            body["target"] = target
        run = RunSummary.model_validate(self.send("POST", f"/api/tickets/{key}/runs", body, 201))
        self.run_ids.append(run.id)
        return run

    def tokens(self) -> list[ClientToken]:
        return [ClientToken.model_validate(t) for t in self.get("/api/me/clients")]


@dataclass
class ClientProcess:
    """A running `worker.py --client` and the token it uses."""

    token: ClientTokenCreated
    proc: subprocess.Popen[bytes]
    log: Path

    def output(self) -> str:
        return self.log.read_text("utf-8", "replace") if self.log.exists() else ""

    def worker_pids(self) -> list[int]:
        """The run workers this client started, from its log."""
        return [int(pid) for pid in WORKER_PID_RE.findall(self.output())]


@dataclass
class World:
    server: Server
    users: list[User] = field(default_factory=list)
    clients: list[ClientProcess] = field(default_factory=list)

    def user(self, username: str) -> User:
        http = httpx.Client(base_url=self.server.url, headers=CSRF, timeout=15)
        r = http.post("/api/auth/login", json={"username": username})
        assert r.status_code == 200, r.text
        root = self.server.root / "clients" / username
        user = User(
            http, self.server.remote, Me.model_validate(r.json()), root / "runs",
            client_root=root,
        )  # fmt: skip
        self.users.append(user)
        return user

    def new_token(self, user: User, name: str) -> ClientTokenCreated:
        body = {"name": name}
        created = ClientTokenCreated.model_validate(user.send("POST", "/api/me/clients", body, 201))
        assert created.server_url == self.server.url
        return created

    def start_client(self, user: User, token: ClientTokenCreated) -> ClientProcess:
        """`python worker.py --client` as a real process, the token in its environment only."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("AGENTOS_")}
        env |= {**GIT_ENV, "AGENTOS_CLIENT_TOKEN": token.token, "PYTHONUNBUFFERED": "1"}
        argv = [
            sys.executable, str(WORKER_PATH), "--client", "--server", self.server.url,
            "--workdir-root", str(user.client_root), "--env-file", str(self.server.env_file),
        ]  # fmt: skip
        assert not any(token.token in arg for arg in argv)
        log = self.server.root / f"client-{token.client.id}.log"
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        with log.open("ab") as out:
            proc = subprocess.Popen(
                argv,
                cwd=self.server.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        client = ClientProcess(token, proc, log)
        self.clients.append(client)
        return client

    def wait_online(self, user: User, client: ClientProcess) -> None:
        """Until the client has polled (its token is online)."""

        def online() -> bool:
            assert client.proc.poll() is None, f"the client exited\n{client.output()}"
            return any(t.online for t in user.tokens() if t.id == client.token.client.id)

        user.wait("the client to poll", online)


@pytest.fixture
def world(server: Server) -> Iterator[World]:
    """Users and clients for one test. Teardown stops the clients, cancels the users' unfinished
    runs and waits for (or ends) every run worker the clients started."""
    w = World(server)
    try:
        yield w
    finally:
        for client in w.clients:
            if client.proc.poll() is None:
                client.proc.kill()
            client.proc.wait(10)
        for user in w.users:
            _settle(user)
        _end_workers([pid for client in w.clients for pid in client.worker_pids()])
        for user in w.users:
            user.http.close()


def _settle(user: User) -> None:
    """Best effort after a failed test: cancel the user's unfinished runs."""
    for run_id in user.run_ids:
        try:
            if user.run(run_id).status not in TERMINAL:
                user.http.post(f"/api/runs/{run_id}/cancel")
                deadline = time.monotonic() + 15
                while user.run(run_id).status not in TERMINAL and time.monotonic() < deadline:
                    time.sleep(0.2)
        except (AssertionError, httpx.HTTPError):
            continue


def _end_workers(pids: list[int]) -> None:
    """Wait for run workers to exit after their runs ended; kill any that outlive the wait."""
    deadline = time.monotonic() + 15
    while any(pid_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    for pid in pids:
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)  # Windows: TerminateProcess
            except OSError:
                continue


def wait_exit(client: ClientProcess) -> int:
    try:
        return client.proc.wait(DEADLINE_S)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the client did not exit within {DEADLINE_S}s\n{client.output()}")


# ------------------------------------------------------------------ the tests


def test_client_runs_gate_edit_continue_while_the_provider_serves_server_runs(
    world: World,
) -> None:
    ann, bob = world.user("ann"), world.user("bob")
    anns = world.start_client(ann, world.new_token(ann, "ann-laptop"))
    bobs = world.start_client(bob, world.new_token(bob, "bob-laptop"))
    t = ann.create_ticket("Say hello on my machine")
    key = t.key
    tasks_md = f".claude/skills/{t.skill_name}/TASKS.md"
    ann.configure(key, {"plan": "plan", "implement": "implement", "review": "review"})

    # Only the owner may send a ticket to their client.
    r = bob.http.post(f"/api/tickets/{key}/runs", json={"target": "client"})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")

    # 1. Dispatch to Ann's client: plan runs there and stops at the gate.
    run1 = ann.dispatch(key, target="client")
    assert (run1.target, run1.stage_ids) == ("client", ["plan"])

    # Meanwhile a ticket dispatched with the default target goes to the configured provider.
    other = ann.create_ticket("Run me on the server").key
    ann.configure(other, {"plan": "plan"})
    server_run = ann.dispatch(other)
    assert (server_run.target, server_run.client_online) == ("server", None)
    ann.wait(
        "the provider to get the server run",
        lambda: server_run.id in world.server.provider.launched,
    )

    detail = ann.wait_run(run1.id, "gated")
    assert [(s.stage_id, s.status, s.exit_code) for s in detail.stage_runs] == [
        ("plan", "succeeded", 0)
    ], ann.diag(run1.id)
    assert (detail.target, detail.launch_ref, detail.launch_url) == ("client", "ann-laptop", None)
    assert (ann.logs / f"{run1.id}.log").is_file()
    t = ann.ticket(key)
    assert t.attention == "needs_you"
    assert t.next_stage == "implement"
    assert t.tasks == TaskProgress(done=0, total=3), ann.diag(run1.id)
    assert ann.files(key)[tasks_md].author_run_id == run1.id

    # 2. Ann edits the plan at the gate: drop one item.
    current = ann.file(key, tasks_md)
    edited = current.content.replace("- [ ] add a greeting\n", "")
    assert edited != current.content
    ann.send(
        "PUT",
        f"/api/tickets/{key}/files/{tasks_md}",
        {"content": edited, "mode": "replace", "base_version": current.version},
    )

    # 3. Continue on the client: implement → review run to the end.
    run2 = ann.dispatch(key, target="client")
    assert (run2.from_stage, run2.stage_ids) == ("implement", ["implement", "review"])
    detail = ann.wait_run(run2.id, "succeeded")
    assert [(s.stage_id, s.status) for s in detail.stage_runs] == [
        ("implement", "succeeded"),
        ("review", "succeeded"),
    ]
    assert detail.launch_ref == "ann-laptop"
    t = ann.ticket(key)
    assert rail(t) == {"plan": "done", "implement": "done", "review": "done"}
    assert t.tasks == TaskProgress(done=2, total=2), ann.diag(run2.id)
    assert "add a greeting" not in ann.file(key, tasks_md).content  # the edit reached the agent
    assert_clean_branch(ann.remote, key)

    # Bob's client never got Ann's runs; no client took the server run.
    assert list(bob.logs.iterdir()) == []  # no run workspace or log
    assert anns.proc.poll() is None
    assert bobs.proc.poll() is None
    server_detail = ann.run(server_run.id)
    assert server_detail.status == "dispatched"
    assert server_detail.launch_url == FakeProvider.ref(server_run.id)


def test_a_client_run_waits_while_the_client_is_offline(world: World, server: Server) -> None:
    cleo = world.user("cleo")
    token = world.new_token(cleo, "cleo-desktop")

    # Cleo's client was leasing and stops: its waiting lease ends with it.
    client = world.start_client(cleo, token)
    world.wait_online(cleo, client)
    client.proc.kill()
    client.proc.wait(10)

    key = cleo.create_ticket("Wait for my machine").key
    cleo.configure(key, {"plan": "plan"})
    run = cleo.dispatch(key, target="client")
    assert run.status == "queued"

    # It stays queued while a lease still waiting on the server would have retried its take.
    until = time.monotonic() + 3 * LEASE_RETRY_S
    while time.monotonic() < until:
        assert cleo.run(run.id).status == "queued"
        time.sleep(POLL_S)

    # The reaper ticks on past the claim timeout: a queued client run is never reaped.
    reaper = Reaper(server.ctx)
    step = timedelta(seconds=REAPER_INTERVAL_S)
    start = utcnow()
    end = start + timedelta(seconds=server.ctx.space.claim_timeout_s) + 2 * step
    now = start
    while now <= end:
        reaped = reaper.tick(now)
        assert run.id not in [r.run_id for r in reaped]
        now += step
    detail = cleo.run(run.id)
    assert (detail.status, detail.launch_ref) == ("queued", None)

    # The client comes back online and starts the run.
    world.start_client(cleo, token)
    detail = cleo.wait_run(run.id, "gated")
    assert [(s.stage_id, s.status) for s in detail.stage_runs] == [("plan", "succeeded")]
    assert (detail.launch_ref, detail.client_online) == ("cleo-desktop", True)
    assert (cleo.logs / f"{run.id}.log").is_file()


def test_revoking_the_token_stops_the_client(world: World) -> None:
    dan = world.user("dan")
    token = world.new_token(dan, "dan-laptop")
    client = world.start_client(dan, token)
    world.wait_online(dan, client)

    dan.send("DELETE", f"/api/me/clients/{token.client.id}", None, 204)

    assert wait_exit(client) == 1, client.output()
    r = httpx.post(
        f"{world.server.url}/api/worker/lease",
        json={"wait_s": 0},
        headers={"Authorization": f"Bearer {token.token}"},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (401, "unauthorized")
    assert dan.tokens() == []
