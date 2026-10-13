-- Workflows: named pipeline presets (request 23B). A ticket copies one's pipeline at creation or
-- at a switch and records which (`workflow_id`, `workflow_name`, `workflow_version`): editing a
-- workflow never changes existing tickets. Exactly one workflow is the default (`is_default`); the
-- startup seed creates "Standard" from the default template's pipeline when there is none
-- (`workflows.service.seed_standard`), so it runs after the template seed on a fresh db too.
-- Names are unique ignoring case (checked in the service; the index is ASCII-only).
-- `templates.pipeline` stays NOT NULL but is no longer read: a template save copies it over.
CREATE TABLE workflows(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  pipeline TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, created_by INTEGER REFERENCES users,
  updated_at TEXT NOT NULL, updated_by INTEGER REFERENCES users);

CREATE UNIQUE INDEX workflows_name ON workflows(lower(name));
CREATE UNIQUE INDEX workflows_one_default ON workflows(is_default) WHERE is_default = 1;

-- NULL for tickets created before workflows. `workflow_id` goes NULL when the workflow is deleted;
-- the name and version stay.
ALTER TABLE tickets ADD COLUMN workflow_id INTEGER REFERENCES workflows ON DELETE SET NULL;
ALTER TABLE tickets ADD COLUMN workflow_name TEXT;
ALTER TABLE tickets ADD COLUMN workflow_version INTEGER;
