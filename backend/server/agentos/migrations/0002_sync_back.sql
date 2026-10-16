-- The `paths.out` prompt variable became `paths.sync_back` (`.agentos/sync_back`). Rendering is
-- strict, so stored text that still names `{{paths.out}}` is rewritten: the current template gets
-- a new version (templates are append-only), workflows and ticket pipelines are updated in place
-- with their version bumped.
INSERT INTO templates(name, version, pipeline, scaffold, defaults, created_at, created_by)
SELECT name, version + 1,
  replace(pipeline, '{{paths.out}}', '{{paths.sync_back}}'),
  replace(scaffold, '{{paths.out}}', '{{paths.sync_back}}'),
  defaults, strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now'), NULL
FROM templates t
WHERE version = (SELECT max(version) FROM templates WHERE name = t.name)
  AND instr(pipeline || scaffold, '{{paths.out}}') > 0;

UPDATE workflows SET pipeline = replace(pipeline, '{{paths.out}}', '{{paths.sync_back}}'),
  version = version + 1
WHERE instr(pipeline, '{{paths.out}}') > 0;

UPDATE tickets SET pipeline = replace(pipeline, '{{paths.out}}', '{{paths.sync_back}}'),
  pipeline_version = pipeline_version + 1
WHERE instr(pipeline, '{{paths.out}}') > 0;
