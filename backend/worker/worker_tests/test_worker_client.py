"""Client mode: `worker.main(["--client", ...])` in-process against the fake server, with an
injected spawn function in place of real run workers."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import pytest

import worker
from worker_tests.worker_support import FakeServer, error, eventually

CLIENT_TOKEN = "c" * 43
LEASE = "/api/worker/lease"
SHA256 = hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest()
TAKE = {"wait_s": 25, "take": True, "alive": [], "gone": []}


@dataclass
class FakeChild:
    pid: int
    done: threading.Event = field(default_factory=threading.Event)

    def poll(self) -> int | None:
        return 0 if self.done.is_set() else None

    def wait(self) -> int:
        self.done.wait()
        return 0


@dataclass
class Spawned:
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    log: Path
    child: FakeChild


class FakeSpawn:
    """Records each spawn. Children finish at once with `finish`, else when the test says so."""

    def __init__(self, finish: bool) -> None:
        self.finish = finish
        self.calls: list[Spawned] = []
        self.lock = threading.Lock()

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str], log: Path) -> FakeChild:
        with self.lock:
            child = FakeChild(pid=1000 + len(self.calls))
            if self.finish:
                child.done.set()
            self.calls.append(Spawned(argv, cwd, env, log, child))
        return child

    def count(self) -> int:
        with self.lock:
            return len(self.calls)


def job_id(run_id: int) -> str:
    return f"{run_id}-1-{run_id:016x}"


def answer(job: dict[str, Any] | None, **server: Any) -> tuple[int, Any]:
    return 200, {"job": job, "protocol": 2, "worker_sha256": SHA256} | server


def assigned(run_id: int, token: str) -> tuple[int, Any]:
    url = "http://elsewhere.invalid"
    return answer(
        {"job_id": job_id(run_id), "run_id": run_id, "server_url": url, "job_token": token}
    )


NOTHING = answer(None)


def answer_leases(server: FakeServer, answers: list[tuple[int, Any] | None]) -> None:
    """Answer leases with `answers` in order (None drops the connection), then 401."""

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        if path != LEASE:
            return None
        if not answers:
            return error(401, "unauthorized")
        answer = answers.pop(0)
        if answer is None:
            raise ConnectionAbortedError("dropped by the test")
        return answer

    server.hook = hook


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> FakeSpawn:
    fake = FakeSpawn(finish=True)
    monkeypatch.setattr(worker, "spawn_detached", fake)
    monkeypatch.setenv("AGENTOS_CLIENT_TOKEN", CLIENT_TOKEN)
    return fake


def run_client(server: FakeServer, root: Path, *extra: str) -> int:
    return worker.main(["--client", "--server", server.url, "--workdir-root", str(root), *extra])


def leases(server: FakeServer) -> int:
    return len(server.bodies(LEASE))


def test_one_worker_per_run_with_the_token_in_its_env_only(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTOS_SECRET", "s")
    monkeypatch.setenv("DROP_ME", "x")
    env_file = tmp_path / "client.env"
    env_file.write_bytes(b"GIT_CONFIG_KEY_0='url.a b.insteadOf'\nDROP_ME\n")
    root = tmp_path / "ws"
    answer_leases(server, [assigned(7, "d1" * 20), NOTHING, assigned(8, "d2" * 20)])

    assert run_client(server, root, "--env-file", str(env_file)) == 1

    runs = root.resolve() / "runs"
    me = str(Path(worker.__file__).resolve())
    assert [(c.argv, c.cwd, c.log) for c in spawn.calls] == [
        (
            [
                *(sys.executable, me, "--server", server.url, "--workdir", str(runs / str(run_id))),
                *("--lock-file", str(runs / f"{job_id(run_id)}.lock")),
            ],
            runs,
            log,
        )
        for run_id, log in ((7, runs / "7.log"), (8, runs / "8.log"))
    ]
    for call, token in zip(spawn.calls, ("d1" * 20, "d2" * 20), strict=True):
        assert call.env["AGENTOS_TOKEN"] == token
        assert [k for k in call.env if k.upper().startswith("AGENTOS_")] == ["AGENTOS_TOKEN"]
        assert call.env["GIT_CONFIG_KEY_0"] == "url.a b.insteadOf"
        assert "DROP_ME" not in call.env
        assert CLIENT_TOKEN not in call.env.values()
        assert not any(token in arg or CLIENT_TOKEN in arg for arg in call.argv)

    assert {r.auth for r in server.requests} == {f"Bearer {CLIENT_TOKEN}"}
    gone = [{**TAKE, "gone": [job_id(run_id)]} for run_id in (7, 8)]  # each finished at once
    assert server.bodies(LEASE) == [TAKE, gone[0], TAKE, gone[1]]
    assert "AGENTOS_CLIENT_TOKEN" not in os.environ


def test_max_runs_leases_only_while_a_slot_is_free(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    spawn.finish = False
    stop = threading.Event()
    next_id = iter(range(1, 100))
    server.hook = lambda path, body: (
        (error(401, "unauthorized") if stop.is_set() else assigned(next(next_id), "d" * 43))
        if path == LEASE
        else None
    )
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(run_client(server, tmp_path / "ws", "--max-runs", "2"))
    )
    thread.start()
    try:
        eventually(lambda: spawn.count() == 2, "two workers")
        seen = leases(server)
        time.sleep(0.2)
        assert (spawn.count(), leases(server)) == (2, seen)

        spawn.calls[0].child.done.set()
        eventually(lambda: spawn.count() == 3, "a third worker once one finished")
        time.sleep(0.1)
        assert spawn.count() == 3
    finally:
        stop.set()
        for call in spawn.calls:
            call.child.done.set()
        thread.join(10)
    assert result == [1]


def test_leasing_goes_on_through_an_unreachable_spell(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    spell: list[tuple[int, Any] | None] = [None, error(503, "x"), None, error(502, "x")] * 3
    spell += [(200, ["not", "a", "lease"]), answer({"run_id": 9})]  # malformed answers
    answer_leases(server, [*spell, assigned(9, "d" * 43)])

    assert run_client(server, tmp_path / "ws") == 1

    workdirs = [c.argv[c.argv.index("--workdir") + 1] for c in spawn.calls]
    assert workdirs == [str((tmp_path / "ws" / "runs" / "9").resolve())]
    assert leases(server) == len(spell) + 2


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_client_stops_on_an_unexpected_4xx(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, status: int
) -> None:
    spawn.finish = False
    answer_leases(server, [assigned(5, "d" * 43), error(status, "x")])

    assert run_client(server, tmp_path / "ws", "--max-runs", "2") == 1

    assert leases(server) == 2
    assert spawn.calls[0].child.poll() is None  # started workers are left running


def test_client_retries_a_transient_4xx(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    answer_leases(server, [error(429, "rate_limited"), error(408, "x"), assigned(6, "d" * 43)])

    assert run_client(server, tmp_path / "ws") == 1

    assert (spawn.count(), leases(server)) == (1, 4)


def test_a_busy_client_sends_heartbeats_that_take_nothing(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "CLIENT_HEARTBEAT_S", 0.05)
    spawn.finish = False
    answer_leases(server, [assigned(5, "d" * 43), *[NOTHING] * 3])

    assert run_client(server, tmp_path / "ws") == 1  # the 401 after the answers

    heartbeat = {"wait_s": 0, "take": False, "alive": [job_id(5)], "gone": []}
    assert server.bodies(LEASE) == [TAKE, *[heartbeat] * 4]
    assert spawn.count() == 1


def test_running_workers_are_listed_as_alive_until_they_finish(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn.finish = False

    def spawn_then_finish_the_first(*args: Any) -> FakeChild:
        child = spawn(*args)
        if spawn.count() == 3:  # worker 5 finishes while worker 7 starts
            spawn.calls[0].child.done.set()
        return child

    monkeypatch.setattr(worker, "spawn_detached", spawn_then_finish_the_first)
    answer_leases(
        server,
        [assigned(5, "d" * 43), assigned(6, "e" * 43), assigned(7, "f" * 43), NOTHING, NOTHING],
    )

    assert run_client(server, tmp_path / "ws", "--max-runs", "3") == 1

    assert [body["alive"] for body in server.bodies(LEASE)] == [
        [],
        [job_id(5)],
        [job_id(5), job_id(6)],
        [job_id(6), job_id(7)],
        [job_id(6), job_id(7)],
        [job_id(6), job_id(7)],
    ]


def test_a_worker_that_exits_during_a_long_poll_is_reported_at_once(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    spawn.finish = False
    held, release = threading.Event(), threading.Event()
    answers = [assigned(5, "d" * 43)]

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        if path != LEASE:
            return None
        if answers:
            return answers.pop(0)
        if not body["take"]:
            release.set()  # the report: let the held lease end the client
            return NOTHING
        held.set()
        release.wait(10)
        return error(401, "unauthorized")

    server.hook = hook
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(run_client(server, tmp_path / "ws", "--max-runs", "2"))
    )
    thread.start()
    try:
        assert held.wait(10)  # the second lease waits on the server
        spawn.calls[0].child.done.set()
        thread.join(10)
    finally:
        release.set()
    assert result == [1]
    assert server.bodies(LEASE) == [
        TAKE,
        {"wait_s": 25, "take": True, "alive": [job_id(5)], "gone": []},
        {"wait_s": 0, "take": False, "alive": [], "gone": [job_id(5)]},
    ]


def test_a_started_worker_is_listed_before_it_holds_its_lock(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spawn.finish = False
    answer_leases(server, [assigned(5, "d" * 43), NOTHING])

    assert run_client(server, tmp_path / "ws", "--max-runs", "2") == 1

    assert not (tmp_path / "ws" / "runs" / f"{job_id(5)}.lock").exists()  # the fake never locks
    assert [body["alive"] for body in server.bodies(LEASE)] == [[], [job_id(5)], [job_id(5)]]
    spawn.calls[0].child.done.set()
    eventually(lambda: f"job {job_id(5)}" in capsys.readouterr().err, "its exit logged")


def test_a_restarted_client_lists_the_workers_that_outlived_it(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    runs = tmp_path / "ws" / "runs"
    runs.mkdir(parents=True)
    (runs / f"{job_id(4)}.lock").write_bytes(b"")  # a worker that ended while no client watched
    survivor = worker.hold_lock(runs / f"{job_id(5)}.lock")
    released: list[bool] = []

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        if path != LEASE:
            return None
        if not released:  # the first lease: then the survivor exits
            released.append(True)
            worker.release_lock(survivor)
            return NOTHING
        return error(401, "unauthorized")

    server.hook = hook
    try:
        assert run_client(server, tmp_path / "ws") == 1  # one slot, full of the survivor
    finally:
        if not released:
            worker.release_lock(survivor)

    assert server.bodies(LEASE) == [
        {"wait_s": 0, "take": False, "alive": [job_id(5)], "gone": [job_id(4)]},  # at once
        {"wait_s": 25, "take": True, "alive": [], "gone": [job_id(5)]},
    ]
    assert not (runs / f"{job_id(4)}.lock").exists()  # acknowledged, then deleted
    assert (runs / f"{job_id(5)}.lock").exists()  # its `gone` got a 401: reported again later


def test_long_lists_go_out_at_most_the_cap_per_lease(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "CLIENT_HEARTBEAT_S", 0.05)
    runs = tmp_path / "ws" / "runs"
    runs.mkdir(parents=True)
    ended = [job_id(n) for n in range(1, 131)]  # their workers ended while no client watched
    for job in ended:
        (runs / f"{job}.lock").write_bytes(b"")
    survivors = [worker.hold_lock(runs / f"{job_id(n)}.lock") for n in range(201, 302)]
    answer_leases(server, [NOTHING, NOTHING])
    try:
        assert run_client(server, tmp_path / "ws") == 1
    finally:
        for lock in survivors:
            worker.release_lock(lock)

    bodies = server.bodies(LEASE)
    assert [(len(b["alive"]), len(b["gone"])) for b in bodies] == [(100, 100), (100, 30), (100, 0)]
    assert sorted(job for b in bodies for job in b["gone"]) == sorted(ended)


def test_a_worker_whose_process_exits_while_its_lock_is_held_stays_alive(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows with a venv: the started process is a stub, and the real worker is its child."""
    spawn.finish = False
    lock = (tmp_path / "ws").resolve() / "runs" / f"{job_id(5)}.lock"
    real: list[IO[bytes]] = []

    def stub_spawn(argv: list[str], cwd: Path, env: dict[str, str], log: Path) -> FakeChild:
        child = spawn(argv, cwd, env, log)
        real.append(worker.hold_lock(lock))  # the real worker runs, holding its lock
        child.done.set()  # while the stub exits
        return child

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        if path != LEASE:
            return None
        n = len(server.bodies(LEASE))
        if n == 1:
            return assigned(5, "d" * 43)
        if n == 2:
            worker.release_lock(real.pop())  # now the real worker exits too
            return NOTHING
        return error(401, "unauthorized")

    monkeypatch.setattr(worker, "spawn_detached", stub_spawn)
    server.hook = hook
    assert run_client(server, tmp_path / "ws", "--max-runs", "2") == 1

    listed = [(b["alive"], b["gone"]) for b in server.bodies(LEASE)]
    assert listed == [([], []), ([job_id(5)], []), ([], [job_id(5)])]


@pytest.mark.parametrize("end", ["exit", "kill"])
def test_a_run_worker_holds_its_lock_until_its_process_ends(
    server: FakeServer, tmp_path: Path, end: str
) -> None:
    lock = tmp_path / f"{job_id(5)}.lock"
    fetching, answer_now = threading.Event(), threading.Event()

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        if path != "/api/worker/job":
            return None
        fetching.set()  # the worker locks before it fetches its job
        answer_now.wait(30)
        return error(410, "run_gone")

    server.hook = hook
    argv = [sys.executable, worker.__file__, "--server", server.url, "--lock-file", str(lock)]
    env = {**os.environ, "AGENTOS_TOKEN": "t" * 43}
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert fetching.wait(30)
        assert worker.lock_held(lock)
        if end == "kill":
            proc.kill()
        answer_now.set()
        assert proc.wait(30) != 0
    finally:
        answer_now.set()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    eventually(lambda: not worker.lock_held(lock), "the OS to release the dead worker's lock")


def test_a_job_id_that_cant_name_a_file_is_a_malformed_answer(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    bad = {"job_id": "../5", "run_id": 5, "server_url": "http://x.invalid", "job_token": "d" * 43}
    answer_leases(server, [answer(bad)])

    assert run_client(server, tmp_path / "ws") == 1
    assert spawn.count() == 0


def test_another_protocol_stops_the_client(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    job = assigned(5, "d" * 43)[1]["job"]
    answer_leases(server, [answer(job, protocol=3)])

    assert run_client(server, tmp_path / "ws") == 1

    assert (spawn.count(), leases(server)) == (0, 1)


def test_another_worker_py_is_mentioned_once(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answer_leases(server, [answer(None, worker_sha256="0" * 64)] * 3)

    assert run_client(server, tmp_path / "ws") == 1

    assert capsys.readouterr().err.count("differs") == 1


def test_refuses_a_workdir_root_inside_a_git_repo(
    server: FakeServer, spawn: FakeSpawn, tmp_path: Path
) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    assert run_client(server, tmp_path / "repo" / "ws") == 2

    assert leases(server) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["--client"],
        ["--client", "--server", "http://127.0.0.1:1", "--token", "t" * 43],
        ["--client", "--server", "http://127.0.0.1:1", "--workdir", "w"],
        ["--client", "--server", "http://127.0.0.1:1", "--manifest", "m", "--outbox", "o"],
        ["--client", "--server", "http://127.0.0.1:1", "--max-runs", "0"],
        ["--client", "--server", "http://127.0.0.1:1", "--max-runs", "101"],
        ["--client", "--server", "ftp://127.0.0.1:1"],
        ["--server", "http://127.0.0.1:1", "--token", "t" * 43, "--max-runs", "2"],
    ],
)
def test_client_arguments_are_checked(spawn: FakeSpawn, argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        worker.parse_args(argv)


def test_client_needs_a_token_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTOS_CLIENT_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        worker.parse_args(["--client", "--server", "http://127.0.0.1:1"])
