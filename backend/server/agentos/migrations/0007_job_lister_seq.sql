-- Pull: the lease `seq` of the instance (`alive_by`) that last listed the job. A lease of that
-- instance changes the listing, or ends the job by leaving it out, only with a higher seq.
ALTER TABLE jobs ADD COLUMN alive_seq INTEGER;
