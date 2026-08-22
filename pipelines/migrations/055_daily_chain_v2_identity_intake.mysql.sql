-- CARDZ 055: durable record of every GemRate intake decision.
--
-- Without this table the morning identity brief has no way to say "I already
-- told you about this card on <date>": it would re-raise the same needs-human
-- rows every single morning until somebody acted, which trains the reader to
-- ignore the message. One row per gemrate id, restated in place, so the brief
-- can ask "has this decision changed since last time" instead of "is this new".
--
-- Why not market_identity_review_queue: its run_id is NOT NULL and points at
-- market_ingest_run, so writing there would force intake to open an ingest run
-- it does not otherwise need, and the queue's own lifecycle (status='pending')
-- belongs to provider ingest, not to a daily classification pass.
--
-- variant_id is nullable on purpose. Most decisions (alias / ruled / ambiguous)
-- name no variant, and a decision that named a variant it did not write would
-- read as a binding.
--
-- Additive and idempotent: the runner replays a half-applied file, so this
-- creates only when absent and never rewrites an existing row's shape.
-- pipelines/daily_chain_v2_contract.py:v2_schema_capabilities derives
-- `schema-055` from this filename; the orchestrator needs no edit.

CREATE TABLE IF NOT EXISTS market_identity_intake_decision (
  gemrate_id CHAR(40) NOT NULL,
  generation_id VARCHAR(64) NOT NULL,
  decision VARCHAR(32) NOT NULL,
  reason_code VARCHAR(191) NOT NULL DEFAULT '',
  variant_id BIGINT UNSIGNED NULL,
  evidence_sha256 CHAR(64) NOT NULL DEFAULT '',
  decided_at DATETIME(6) NOT NULL,
  PRIMARY KEY (gemrate_id),
  KEY ix_intake_generation_decision (generation_id, decision),
  KEY ix_intake_variant (variant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('055');
