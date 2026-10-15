"""A fake agent runtime for the end-to-end test (SPEC §14): no network, no LLM.

Configured as `[sys.executable, <this file>]` with `prompt_via = "stdin"` as the runtime the test
seeds. It reads the prompt from stdin and acts on the LAST line of the form
`fake-agent: <mode>` (the test appends one to each stage's prompt through the pipeline API):

  plan       write a 3-item checklist to TASKS.md, append to learnings.log
  implement  tick every TASKS.md item, append to learnings.log, write src/hello.py and append a
             line to AGENTS.md outside the agentos block (a tracked file: must not be committed)
  review     write REVIEW.md in the skill dir
  fail       print, then exit 1
  slow       print its pid, then print a line every 0.2 s for up to 120 s (for cancel)

It runs in the workspace (cwd) and finds the context through AGENTOS_SKILL_DIR /
AGENTOS_TICKET_KEY / AGENTOS_RUN_ID. Like a real agent CLI it prints stream-json-like lines on
stdout and a line on stderr. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

MODE_RE = re.compile(r"^fake-agent:\s*([a-z]+)\s*$", re.MULTILINE)


def emit(**event: object) -> None:
    print(json.dumps(event), flush=True)


def append(path: Path, text: str) -> None:
    with path.open("ab") as fh:
        fh.write(text.encode("utf-8"))


def plan(skill: Path, run_id: str) -> None:
    tasks = "# Tasks\n- [ ] write src/hello.py\n- [ ] add a greeting\n- [ ] review the change\n"
    (skill / "TASKS.md").write_bytes(tasks.encode("utf-8"))
    append(skill / "learnings.log", f"run {run_id}: planned three steps\n")


def implement(skill: Path, key: str, run_id: str) -> None:
    tasks = skill / "TASKS.md"
    tasks.write_bytes(tasks.read_bytes().replace(b"- [ ]", b"- [x]"))
    append(skill / "learnings.log", f"run {run_id}: implemented the plan\n")
    Path("src").mkdir(exist_ok=True)
    Path("src/hello.py").write_bytes(f'GREETING = "hello from {key}"\n'.encode())
    append(Path("AGENTS.md"), "\nNote from the fake agent (outside the agentos block).\n")


def review(skill: Path, key: str) -> None:
    (skill / "REVIEW.md").write_bytes(f"# Review of {key}\n\nLooks good.\n".encode())


def slow() -> None:
    emit(type="system", subtype="pid", pid=os.getpid())
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        emit(type="assistant", message="still thinking")
        time.sleep(0.2)


def main() -> int:
    prompt = sys.stdin.read()
    modes = MODE_RE.findall(prompt)
    if not modes:
        print("fake-agent: no `fake-agent: <mode>` line in the prompt", file=sys.stderr)
        return 2
    mode = modes[-1]
    skill = Path(os.environ["AGENTOS_SKILL_DIR"])
    key = os.environ["AGENTOS_TICKET_KEY"]
    run_id = os.environ["AGENTOS_RUN_ID"]
    emit(type="system", subtype="init", mode=mode, ticket=key, run_id=run_id)
    print(f"fake-agent {mode} on {key} (run {run_id})", file=sys.stderr, flush=True)
    if mode == "plan":
        plan(skill, run_id)
    elif mode == "implement":
        implement(skill, key, run_id)
    elif mode == "review":
        review(skill, key)
    elif mode == "fail":
        emit(type="result", subtype="error", result="the fake agent was told to fail")
        return 1
    elif mode == "slow":
        slow()
    else:
        print(f"fake-agent: unknown mode {mode!r}", file=sys.stderr)
        return 2
    emit(type="result", subtype="success", result=f"{mode} done", total_cost_usd=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
