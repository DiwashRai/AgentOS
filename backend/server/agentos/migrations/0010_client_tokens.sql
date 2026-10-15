-- 0010: client tokens. A user's own client (`worker.py --client`) polls with one and runs the
-- runs that user dispatched to "My client" (`runs.launcher = 'client'`). Only the token's SHA-256
-- hex digest is stored; revoking deletes the row. `last_seen_at` = the token's latest poll.
CREATE TABLE client_tokens(id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, last_seen_at TEXT);

CREATE INDEX client_tokens_user ON client_tokens(user_id);

-- Each user's client queue: their queued client runs, oldest first.
CREATE INDEX runs_client_queue ON runs(created_by, id) WHERE launcher = 'client' AND status = 'queued';
