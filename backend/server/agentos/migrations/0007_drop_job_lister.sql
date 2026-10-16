-- A client now reports the jobs it saw end (`gone`), so no lease ends a job by leaving it out
-- and the lister of a job is no longer recorded.
ALTER TABLE jobs DROP COLUMN alive_by;
