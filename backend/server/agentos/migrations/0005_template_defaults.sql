-- The Space-wide defaults for agent stages, per template version: a JSON TemplateDefaults
-- ({"runtime": ..., "model": ...}; null or absent = unset). Earlier versions have none.
ALTER TABLE templates ADD COLUMN defaults TEXT NOT NULL DEFAULT '{}';
