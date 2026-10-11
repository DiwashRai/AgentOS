-- How a ticket was closed (POST /api/tickets/{key}/resolve): `completed` (state done) or
-- `abandoned` (state closed), an optional note, who and when (clock.to_db). All NULL = unresolved:
-- an open ticket, or one marked done/closed through PATCH (any PATCH that changes `state` clears
-- them).
ALTER TABLE tickets ADD COLUMN resolution_outcome TEXT;
ALTER TABLE tickets ADD COLUMN resolution_note TEXT;
ALTER TABLE tickets ADD COLUMN resolved_by INTEGER REFERENCES users(id);
ALTER TABLE tickets ADD COLUMN resolved_at TEXT;
