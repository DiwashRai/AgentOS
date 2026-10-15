-- The schema. Never edit a shipped migration; change the schema by adding 0002_*.sql.
-- Enum columns have no CHECK constraints: the Literals in models.py are the only definition.
-- Timestamps are ISO-8601 UTC text (agentos.clock.to_db). JSON columns are validated on write.

CREATE TABLE users(id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, email TEXT, avatar_url TEXT,
  role TEXT NOT NULL DEFAULT 'viewer', disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, last_login_at TEXT);          -- last_login_at NULL = known (e.g. Jira assignee), never signed in

CREATE TABLE user_identities(provider TEXT NOT NULL, subject TEXT NOT NULL,
  -- ('dev', username) | ('jira', accountId or DC key)
  user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, created_at TEXT NOT NULL,
  PRIMARY KEY(provider, subject));

CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);

CREATE TABLE key_counters(prefix TEXT PRIMARY KEY, next INTEGER NOT NULL);

-- Append-only; current = max(version) per name. `pipeline` is filled by the startup seed and
-- seeds the "Standard" workflow; a template save copies it over. `defaults` is a JSON
-- TemplateDefaults for agent stages ({"runtime": ..., "model": ...}; null or absent = unset).
CREATE TABLE templates(id INTEGER PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
  pipeline TEXT NOT NULL, scaffold TEXT NOT NULL, created_at TEXT NOT NULL,
  created_by INTEGER REFERENCES users, defaults TEXT NOT NULL DEFAULT '{}', UNIQUE(name, version));

-- `repo`: by name from the Space's repo catalog (NULL = no repo); its URL and default branch are
-- resolved from the catalog. `base_branch`: only the ticket's own branch (NULL = none of its own).
-- `base_branch_source`: 'default' (no own branch: the repo's default_branch applies, and may be
-- unset), 'manual' (set by hand, by `base_branch_set_by`) or 'mapping' (set or left blank by a
-- Jira field mapping; blank stays blank). `base_branch_source_value`: the raw source field text a
-- mapping read; `base_branch_reason`: why a mapping left it blank. `base_branch_locked_by_run`:
-- the first run that got past workspace setup (its first `stage_started`); while set, the base
-- branch and repo can't change.
-- `resolution_*`, `resolved_by`, `resolved_at`: how a ticket was closed (POST
-- /api/tickets/{key}/resolve): `completed` (state done) or `abandoned` (state closed), an optional
-- note, who and when. All NULL = unresolved (any PATCH that changes `state` clears them).
-- `pr_url`: the PR for the ticket's branch, as last reported by a worker.
-- `workflow_*`: the workflow whose pipeline the ticket copied (at creation or a switch);
-- `workflow_id` goes NULL when the workflow is deleted, the name and version stay.
CREATE TABLE tickets(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT 'task', priority TEXT NOT NULL DEFAULT 'normal',
  labels TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'open', base_branch TEXT,
  pipeline TEXT NOT NULL, pipeline_version INTEGER NOT NULL DEFAULT 1, owner_id INTEGER REFERENCES users,
  source TEXT, external_id TEXT, source_url TEXT,
  tasks_done INTEGER, tasks_total INTEGER,                 -- from parse_plan(TASKS.md) on every version write
  created_by INTEGER REFERENCES users, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  pr_url TEXT, repo TEXT,
  resolution_outcome TEXT, resolution_note TEXT, resolved_by INTEGER REFERENCES users(id), resolved_at TEXT,
  base_branch_source TEXT NOT NULL DEFAULT 'default', base_branch_source_value TEXT, base_branch_reason TEXT,
  base_branch_set_by INTEGER REFERENCES users(id), base_branch_locked_by_run INTEGER REFERENCES runs(id),
  workflow_id INTEGER REFERENCES workflows ON DELETE SET NULL, workflow_name TEXT, workflow_version INTEGER,
  UNIQUE(source, external_id));

CREATE TABLE file_versions(id INTEGER PRIMARY KEY, owner_type TEXT NOT NULL,   -- 'ticket' | 'library'
  owner_id INTEGER NOT NULL, path TEXT NOT NULL, version INTEGER NOT NULL, content TEXT,   -- NULL = deleted
  mode TEXT NOT NULL DEFAULT 'replace',
  author_user_id INTEGER REFERENCES users, author_run_id INTEGER REFERENCES runs, author_stage TEXT,
  created_at TEXT NOT NULL, UNIQUE(owner_type, owner_id, path, version));

CREATE TABLE runs(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets, status TEXT NOT NULL,
  from_stage TEXT NOT NULL, stage_ids TEXT NOT NULL,
  manifest TEXT,                                           -- built and stored at claim; NULL before
  time_budget_s INTEGER NOT NULL,
  launcher TEXT NOT NULL, launch_ref TEXT, dispatch_token_hash TEXT UNIQUE, worker_token_hash TEXT UNIQUE,
  cancel_requested_at TEXT, error TEXT, created_by INTEGER REFERENCES users,
  created_at TEXT NOT NULL, dispatched_at TEXT, started_at TEXT, last_seen_at TEXT, finished_at TEXT);

CREATE UNIQUE INDEX runs_one_active ON runs(ticket_id) WHERE status IN('queued','dispatched','running');

-- One row per stage execution, in execution order. A stage in a loop block runs once per round,
-- each its own row with its `round` (1 outside loops); `verdict` is a check's verdict in that
-- round ('pass' | 'fail'; NULL for other stages and until it is known).
CREATE TABLE stage_runs(run_id INTEGER NOT NULL REFERENCES runs ON DELETE CASCADE, idx INTEGER NOT NULL,
  stage_id TEXT NOT NULL,                                  -- repeats within a run across loop rounds
  status TEXT NOT NULL DEFAULT 'pending',
  exit_code INTEGER, reason TEXT, started_at TEXT, finished_at TEXT,
  round INTEGER NOT NULL DEFAULT 1, verdict TEXT, PRIMARY KEY(run_id, idx));

CREATE TABLE run_events(run_id INTEGER NOT NULL REFERENCES runs ON DELETE CASCADE, seq INTEGER NOT NULL, ts TEXT NOT NULL,
  stage_id TEXT, type TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(run_id, seq));

CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets,
  user_id INTEGER NOT NULL REFERENCES users, body TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE INDEX file_versions_owner_path ON file_versions(owner_type, owner_id, path);
CREATE INDEX runs_ticket ON runs(ticket_id, id);
CREATE INDEX chat_messages_ticket ON chat_messages(ticket_id, id);

-- Settings edited in the app by admins: one JSON document per area, overwritten on every save
-- (no history). `version` guards concurrent saves (409 stale_version). `updated_by` NULL = the
-- built-in defaults a fresh database starts with.
CREATE TABLE settings(area TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL,
  updated_at TEXT NOT NULL, updated_by INTEGER REFERENCES users(id));

-- Workflows: named pipeline presets. A ticket copies one's pipeline at creation or at a switch:
-- editing a workflow never changes existing tickets. Exactly one workflow is the default
-- (`is_default`); with none at all, the startup seed creates "Standard" from the default
-- template's pipeline. Names are unique ignoring case (checked in the service; the index is
-- ASCII-only).
CREATE TABLE workflows(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  pipeline TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, created_by INTEGER REFERENCES users,
  updated_at TEXT NOT NULL, updated_by INTEGER REFERENCES users);

CREATE UNIQUE INDEX workflows_name ON workflows(lower(name));
CREATE UNIQUE INDEX workflows_one_default ON workflows(is_default) WHERE is_default = 1;

-- Client tokens. A user's own client (`worker.py --client`) polls with one and runs the runs that
-- user dispatched to "My client" (`runs.launcher = 'client'`). Only the token's SHA-256 hex
-- digest is stored; revoking deletes the row. `last_seen_at` = the token's latest poll.
CREATE TABLE client_tokens(id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, last_seen_at TEXT);

CREATE INDEX client_tokens_user ON client_tokens(user_id);

-- Each user's client queue: their queued client runs, oldest first.
CREATE INDEX runs_client_queue ON runs(created_by, id) WHERE launcher = 'client' AND status = 'queued';
