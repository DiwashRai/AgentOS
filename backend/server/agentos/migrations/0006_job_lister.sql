-- Pull: the SHA-256 of the client instance (one `worker.py --client` process) whose lease last
-- listed the job as alive. Only that instance's lease can end the job by leaving it out.
ALTER TABLE jobs ADD COLUMN alive_by TEXT;
