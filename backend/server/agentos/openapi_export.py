"""Write the OpenAPI schema to a file: python -m agentos.openapi_export <path>"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentos.main import create_app


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m agentos.openapi_export <output.json>")
    schema = create_app().openapi()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    Path(sys.argv[1]).write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
