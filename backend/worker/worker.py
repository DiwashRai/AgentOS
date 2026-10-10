"""AgentOS worker: claims a run, executes its stages, syncs context back, then exits.

Single file, Python >= 3.11 standard library only (enforced by a test). Served by the server at
GET /worker.py so runners always get the matching version. See SPEC §9.
"""

from __future__ import annotations

import argparse
import os
import sys

PROTOCOL = 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="worker.py", description=__doc__)
    parser.add_argument(
        "--server", required=True, help="AgentOS server URL, e.g. http://127.0.0.1:8000"
    )
    parser.add_argument("--token", help="dispatch token (default: $AGENTOS_TOKEN)")
    parser.add_argument("--workdir", help="workspace directory (default: ./agentos-run-<run_id>)")
    args = parser.parse_args(argv)
    args.token = args.token or os.environ.get("AGENTOS_TOKEN")
    if not args.token:
        parser.error("a dispatch token is required (--token or AGENTOS_TOKEN)")
    return args


def main(argv: list[str] | None = None) -> int:
    parse_args(sys.argv[1:] if argv is None else argv)
    print("worker: not implemented yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
