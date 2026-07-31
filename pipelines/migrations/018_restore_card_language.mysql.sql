-- Restore card_language on catalog identity (reverses the wrong cut of 015 for ranking).
--
-- Why: collector numbers collide across languages (e.g. JP SV2a Charizard ex 201/165
-- vs EN MEW Alakazam ex 201/165). Without language, source binding and image auto-QC
-- merge distinct printings. Rank-4 Rare Candy vs Mega Charizard is the same class of
-- failure when marketplace titles also pollute names.
--
-- Codes are CARDZ-canonical (see pipelines/g10_ingest.normalize_language /
-- g10_public_snapshot.canonical_card_language), NOT free text:
--   en | ja | ko | zhCN | zhTW
-- Aliases at ingest only: jp/jpn→ja, eng→en, zh-cn/zh-hans→zhCN, zh-tw/zh-hant→zhTW.
--
-- Phase 1: nullable column + indexes. Backfill is a separate operator job.
-- Opaque_id rehash is NOT done here (would re-key the whole catalog).

ALTER TABLE catalog_variant
    ADD COLUMN card_language VARCHAR(8) NULL
        COMMENT 'Canonical card print language: en|ja|ko|zhCN|zhTW'
        AFTER tcg_code,
    DROP INDEX ix_catalog_variant_printing,
    ADD KEY ix_catalog_variant_printing (tcg_code, card_language, set_name, collector_number),
    ADD KEY ix_catalog_variant_lang (tcg_code, card_language, identity_status);

ALTER TABLE catalog_printing_identity
    ADD COLUMN card_language VARCHAR(8) NULL
        COMMENT 'Canonical card print language: en|ja|ko|zhCN|zhTW'
        AFTER tcg_code;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('018');
