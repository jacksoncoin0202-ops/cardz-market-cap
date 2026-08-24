-- CARDZ 059: let one business date carry more than one published generation,
-- so a date that was published from bad inputs can be re-run in place.
--
-- publication_outbox is the publication lock: UNIQUE(business_date,event_type)
-- is what makes daily_chain_v2_db.insert_live_event refuse "live.confirmed key
-- already belongs to another generation", and that refusal is the reason a
-- business date could never be published twice. The lock is not being removed
-- -- it is being told what a legitimate second publication looks like: the same
-- date, the same event type, a DIFFERENT generation. A rerun that produced the
-- same generation still collides, and so does an ordinary rerun, because it
-- reuses the same event_key and uq_publication_outbox_event is untouched.
--
-- `superseded` is the reader's flag, not the writer's: the supersede publish
-- marks the older row in the same transaction as the insert, so nothing can
-- observe one date with two rows that both claim to be live.
--
-- Additive and idempotent (the runner replays a half-applied file). No row is
-- deleted and no existing row's generation is rewritten; the only index that
-- goes away is the narrower form of the one being added.
-- pipelines/daily_chain_v2_contract.py:v2_schema_capabilities derives
-- `schema-059` from this filename; the orchestrator needs no edit.

SET @have_superseded := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'publication_outbox'
      AND COLUMN_NAME = 'superseded'
);
SET @ddl := IF(
    @have_superseded > 0,
    'SELECT 1',
    'ALTER TABLE publication_outbox
       ADD COLUMN superseded TINYINT(1) NOT NULL DEFAULT 0 AFTER degraded'
);
PREPARE add_outbox_superseded FROM @ddl;
EXECUTE add_outbox_superseded;
DEALLOCATE PREPARE add_outbox_superseded;

SET @have_narrow := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'publication_outbox'
      AND INDEX_NAME = 'uq_publication_outbox_business_type'
);
SET @ddl := IF(
    @have_narrow = 0,
    'SELECT 1',
    'ALTER TABLE publication_outbox
       DROP INDEX uq_publication_outbox_business_type'
);
PREPARE drop_outbox_business_type FROM @ddl;
EXECUTE drop_outbox_business_type;
DEALLOCATE PREPARE drop_outbox_business_type;

SET @have_wide := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'publication_outbox'
      AND INDEX_NAME = 'uq_publication_outbox_business_type_generation'
);
SET @ddl := IF(
    @have_wide > 0,
    'SELECT 1',
    'ALTER TABLE publication_outbox
       ADD UNIQUE KEY uq_publication_outbox_business_type_generation
       (business_date, event_type, generation_id)'
);
PREPARE add_outbox_business_type_generation FROM @ddl;
EXECUTE add_outbox_business_type_generation;
DEALLOCATE PREPARE add_outbox_business_type_generation;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('059');
