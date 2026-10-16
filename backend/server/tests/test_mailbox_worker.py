"""Mailbox mode end to end without GitHub or Artifactory: the server sends a run, the real
`worker.py --manifest --outbox` runs it, and the server replays the outbox it wrote."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentos.clock import utcnow
from agentos.launchers.base import Provider
from agentos.launchers.local import WORKER_PATH
from agentos.mailbox import dispatcher as mailbox_dispatcher
from agentos.mailbox import service as mailbox_service
from agentos.mailbox.reaper import MailboxReaper
from agentos.models import TicketDetail
from tests.mailbox_support import FakeMailbox
from tests.run_support import FakeProvider, RunDriver

CreateTicket = Callable[..., TicketDetail]

# The "agent": ticks the ticket's TASKS.md in the skill dir the worker points it at.
AGENT = (
    "import os, pathlib, sys\n"
    "p = pathlib.Path(os.environ['AGENTOS_SKILL_DIR']) / 'TASKS.md'\n"
    "p.write_text('# Tasks\\n- [x] done by the agent\\n', encoding='utf-8')\n"
    "print('agent ran: ' + sys.stdin.read()[:20])\n"
)


@pytest.fixture
def providers(provider: FakeProvider) -> dict[str, Provider]:
    """Overrides conftest's: mailbox runs go through GitHub."""
    return {"github": provider}


def test_a_real_worker_outbox_replays_into_the_run(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    tmp_path: Path,
) -> None:
    ticket = create_ticket("Mailbox end to end", repo=None)
    mailbox = FakeMailbox()
    runs.ctx.route, runs.ctx.mailbox = mailbox_service.ROUTE, mailbox
    run_id = runs.queue(ticket.key).id
    assert mailbox_dispatcher.tick(runs.ctx, mailbox) == [run_id]

    # What the fetch job hands the work job: the manifest the server sent (agent argv swapped for
    # the stand-in above, since no agent CLI runs in tests).
    [(_, folder, sent)] = mailbox.sent
    manifest = json.loads(sent)
    for stage in manifest["stages"]:
        if stage["kind"] == "agent":
            stage["argv"] = [sys.executable, "-c", AGENT]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    outbox = tmp_path / "outbox.jsonl"

    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("AGENTOS_")}
    env["AGENTOS_PR_LOOKUP"] = "0"
    done = subprocess.run(
        [sys.executable, str(WORKER_PATH), "--manifest", str(manifest_path),
         "--outbox", str(outbox), "--workdir", str(tmp_path / "work")],
        capture_output=True, env=env, timeout=120, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout.decode() + done.stderr.decode()
    header = json.loads(outbox.read_bytes().splitlines()[0])
    assert header == {"outbox": 1, "run_id": run_id}

    # What the publish job uploads, then the reaper's next tick.
    mailbox.put_outbox(run_id, outbox.read_bytes())
    mailbox.set_status(run_id, "ended", "success")
    assert MailboxReaper(runs.ctx, mailbox).tick(utcnow()) == {run_id: "replayed"}

    detail = runs.get(run_id)
    assert detail.status in ("gated", "succeeded"), detail.error
    assert detail.stage_runs[0].status == "succeeded"
    tasks = f".claude/skills/{ticket.skill_name}/TASKS.md"
    r = authed.get(f"/api/tickets/{ticket.key}/files/{tasks}")
    assert "done by the agent" in r.json()["content"]
    logs = authed.get(f"/api/runs/{run_id}/events").json()
    assert any("agent ran" in json.dumps(e) for e in logs)
    assert folder in mailbox.deleted
