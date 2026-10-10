-- 0001: the M0 schema (SPEC §5). Never edit once applied; add 0002_*.sql instead.
-- Enum columns have no CHECK constraints: the Literals in models.py are the only definition.
-- Timestamps are ISO-8601 UTC text (agentos.clock.to_db). JSON columns are validated on write.

CREATE TABLE users(id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, email TEXT, avatar_url TEXT,
  role TEXT NOT NULL DEFAULT 'viewer', disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, last_login_at TEXT);          -- last_login_at NULL = known (e.g. Jira assignee), never signed in

CREATE TABLE user_identities(provider TEXT NOT NULL, subject TEXT NOT NULL,
  -- ('dev', username) | ('jira', accountId or DC key); later ('oidc', sub), ('github', login) for PR reviewers
  user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, created_at TEXT NOT NULL,
  PRIMARY KEY(provider, subject));

CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);

CREATE TABLE key_counters(prefix TEXT PRIMARY KEY, next INTEGER NOT NULL);

CREATE TABLE templates(id INTEGER PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
  pipeline TEXT NOT NULL, scaffold TEXT NOT NULL, created_at TEXT NOT NULL,
  created_by INTEGER REFERENCES users, UNIQUE(name, version));   -- append-only; current = max(version) per name

CREATE TABLE tickets(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, slug TEXT NOT NULL, title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT 'task', priority TEXT NOT NULL DEFAULT 'normal',
  labels TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'open',
  workspace_mode TEXT NOT NULL, repo_url TEXT, base_branch TEXT,
  template_id INTEGER REFERENCES templates,                -- the exact template version it was copied from
  pipeline TEXT NOT NULL, pipeline_version INTEGER NOT NULL DEFAULT 1, owner_id INTEGER REFERENCES users,
  source TEXT, external_id TEXT, source_url TEXT,
  tasks_done INTEGER, tasks_total INTEGER,                 -- from parse_plan(TASKS.md) on every version write
  created_by INTEGER REFERENCES users, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(source, external_id));

CREATE TABLE file_versions(id INTEGER PRIMARY KEY, owner_type TEXT NOT NULL,   -- 'ticket' | 'library'
  owner_id INTEGER NOT NULL, path TEXT NOT NULL, version INTEGER NOT NULL, content TEXT,   -- NULL = deleted
  mode TEXT NOT NULL DEFAULT 'replace',
  author_user_id INTEGER REFERENCES users, author_run_id INTEGER REFERENCES runs, author_stage TEXT,
  created_at TEXT NOT NULL, UNIQUE(owner_type, owner_id, path, version));

CREATE TABLE runs(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets, status TEXT NOT NULL,
  from_stage TEXT NOT NULL, stage_ids TEXT NOT NULL,
  manifest TEXT,                                           -- built and stored at claim (§8); NULL before
  time_budget_s INTEGER NOT NULL,
  launcher TEXT NOT NULL, launch_ref TEXT, dispatch_token_hash TEXT UNIQUE, worker_token_hash TEXT UNIQUE,
  cancel_requested_at TEXT, error TEXT, created_by INTEGER REFERENCES users,
  created_at TEXT NOT NULL, dispatched_at TEXT, started_at TEXT, last_seen_at TEXT, finished_at TEXT);

CREATE UNIQUE INDEX runs_one_active ON runs(ticket_id) WHERE status IN('queued','dispatched','running');

CREATE TABLE stage_runs(run_id INTEGER NOT NULL REFERENCES runs ON DELETE CASCADE, idx INTEGER NOT NULL,
  stage_id TEXT NOT NULL,                                  -- may repeat within a run (future review→fix loops)
  status TEXT NOT NULL DEFAULT 'pending',
  exit_code INTEGER, reason TEXT, started_at TEXT, finished_at TEXT, PRIMARY KEY(run_id, idx));

CREATE TABLE run_events(run_id INTEGER NOT NULL REFERENCES runs ON DELETE CASCADE, seq INTEGER NOT NULL, ts TEXT NOT NULL,
  stage_id TEXT, type TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(run_id, seq));

CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets,
  user_id INTEGER NOT NULL REFERENCES users, body TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE INDEX file_versions_owner_path ON file_versions(owner_type, owner_id, path);
CREATE INDEX runs_ticket ON runs(ticket_id, id);
CREATE INDEX chat_messages_ticket ON chat_messages(ticket_id, id);
