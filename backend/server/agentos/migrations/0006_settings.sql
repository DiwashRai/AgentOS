-- Settings edited in the app by admins (Settings › Space, Repos, Intake; SPEC "Settings in the
-- app"): one JSON document per area (`space`, `repos`, `intake`), overwritten on every save (no
-- history). `version` guards concurrent saves (409 stale_version). `updated_by` NULL = seeded on
-- first start from agentos.toml + env. Timestamps via clock.to_db.
CREATE TABLE settings(area TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL,
  updated_at TEXT NOT NULL, updated_by INTEGER REFERENCES users(id));
