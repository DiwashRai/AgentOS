"""SQLite access. The ONLY module that imports sqlite3 (import-linter enforces this).

Services use these re-exports, so swapping to Postgres later touches one module.
"""

from __future__ import annotations

import sqlite3

Conn = sqlite3.Connection
IntegrityError = sqlite3.IntegrityError
