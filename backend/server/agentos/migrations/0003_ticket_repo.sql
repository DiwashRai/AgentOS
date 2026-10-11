-- The ticket's repo, by name from the Space's repo catalog (agentos.toml [[repos]]); NULL = no repo.
-- Its URL and default branch are resolved from the catalog, so `repo_url` is no longer written
-- (kept for the startup backfill that matches old tickets' URLs to catalog entries).
-- `base_branch` now holds only a ticket's own choice (NULL = the repo's default_branch).
ALTER TABLE tickets ADD COLUMN repo TEXT;
