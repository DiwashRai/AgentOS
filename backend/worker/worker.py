"""The AgentOS worker's entry script: runs `agentos_worker.main` (the package beside it).

The server serves this script (as `__main__.py`) and the package as one zip named worker.py;
from a checkout, run it in place. Copied alone, it can't find the package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # the package's folder, or the zip itself

if __name__ == "__main__":
    if importlib.util.find_spec("agentos_worker") is None:
        sys.exit(
            "worker.py: no agentos_worker package beside it: run it inside backend/worker/, or "
            "download worker.py from <server>/api/worker/worker.py"
        )
    from agentos_worker.main import main

    sys.exit(main())
