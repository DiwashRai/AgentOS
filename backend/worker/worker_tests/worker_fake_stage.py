"""A fake agent for worker tests: `python worker_fake_stage.py ACTION[=ARG] ...`, run in the
workspace (cwd). Actions run in order:

  edit            tick TASKS.md, append to learnings.log, write src/app.py, and edit AGENTS.md
                  outside the agentos block
  write=P:TEXT    write TEXT to path P            rm=P            delete path P
  selfcommit      force-add CLAUDE.md and commit it (an agent ignoring the rules)
  forcecommit=P   force-add path P and commit it
  copyblock       copy AGENTS.md (block included) into docs/copy.md
  symlink=TARGET  create SKILL_DIR/link.md pointing at TARGET
  bgchild=PIDFILE start a child that holds stdout/stderr open for 30 s, write its pid, return
  sleep=N         print "started", then sleep N seconds
  print=TEXT      print TEXT                      long=N          print N x's, then "after"
  env             print the AGENTOS_* environment as JSON
  stdin           echo stdin to stdout            exit=N          exit with code N
  savein=P        write stdin (the prompt) to P, as UTF-8
  saveargs=P      write the arguments after this one to P as a JSON list, and stop: they are
                  data (an argument-mode prompt), not actions
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _skill_dir() -> Path:
    return Path(os.environ["AGENTOS_SKILL_DIR"])


def _edit() -> None:
    skill = _skill_dir()
    tasks = skill / "TASKS.md"
    tasks.write_bytes(tasks.read_bytes().replace(b"- [ ]", b"- [x]", 1))
    with (skill / "learnings.log").open("ab") as fh:
        fh.write(b"expiry is computed in minutes\n")
    Path("src").mkdir(exist_ok=True)
    Path("src/app.py").write_bytes(b"EXPIRY_DAYS = 14\n")
    with Path("AGENTS.md").open("ab") as fh:
        fh.write(b"\nAgent was here.\n")


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _act(action: str) -> None:
    name, _, arg = action.partition("=")
    if name == "edit":
        _edit()
    elif name == "write":
        path, _, text = arg.partition(":")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(text.encode())
    elif name == "rm":
        Path(arg).unlink()
    elif name in ("selfcommit", "forcecommit"):
        _git("add", "-f", arg or "CLAUDE.md")
        _git("commit", "-q", "--no-verify", "-m", "agent commit")
    elif name == "copyblock":
        Path("docs").mkdir(exist_ok=True)
        Path("docs/copy.md").write_bytes(Path("AGENTS.md").read_bytes())
    elif name == "symlink":
        (_skill_dir() / "link.md").symlink_to(arg)
    elif name == "bgchild":
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        Path(arg).write_text(str(child.pid))
        print("parent done", flush=True)
    elif name == "sleep":
        print("started", flush=True)
        time.sleep(float(arg))
    elif name == "print":
        print(arg, flush=True)
    elif name == "long":
        print("x" * int(arg), flush=True)
        print("after", flush=True)
    elif name == "env":
        env = {k: v for k, v in os.environ.items() if k.upper().startswith(("AGENTOS_", "GIT_"))}
        print(json.dumps(env, sort_keys=True), flush=True)
    elif name == "stdin":
        sys.stdout.write(sys.stdin.read())
        sys.stdout.flush()
    elif name == "exit":
        sys.exit(int(arg))
    elif name == "savein":
        Path(arg).write_bytes(sys.stdin.buffer.read())
    else:
        raise SystemExit(f"unknown action {action!r}")


if __name__ == "__main__":
    args = sys.argv[1:]
    for i, item in enumerate(args):
        if item.startswith("saveargs="):
            Path(item.partition("=")[2]).write_text(json.dumps(args[i + 1 :]), encoding="utf-8")
            break
        _act(item)
