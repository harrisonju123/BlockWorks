-- Cache token tracking for Anthropic prompt caching visibility.
-- Existing rows default to 0 (no backfill needed).
ALTER TABLE llm_events ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_events ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0;
