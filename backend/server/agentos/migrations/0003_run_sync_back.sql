-- A run's sync_back zip, uploaded by its worker at the end of the job (one per run; a repeat
-- upload replaces it). `bytes` is the zip's size, `files` the number of files in it.
CREATE TABLE run_sync_back(run_id INTEGER PRIMARY KEY REFERENCES runs ON DELETE CASCADE,
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, files INTEGER NOT NULL, data BLOB NOT NULL,
  created_at TEXT NOT NULL);
