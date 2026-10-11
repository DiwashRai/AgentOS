-- The PR for the ticket's branch, as last reported by a worker (POST /api/worker/complete).
ALTER TABLE tickets ADD COLUMN pr_url TEXT;
