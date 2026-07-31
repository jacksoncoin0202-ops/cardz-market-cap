-- Card language is not part of CARDZ Market Cap canonical printing identity.
-- Story translation locale_code remains unchanged.

ALTER TABLE catalog_variant
    DROP INDEX ix_catalog_variant_printing,
    DROP INDEX ix_catalog_variant_market,
    DROP COLUMN card_language,
    ADD KEY ix_catalog_variant_printing (tcg_code, set_name, collector_number),
    ADD KEY ix_catalog_variant_market (tcg_code, identity_status);

ALTER TABLE catalog_printing_identity
    DROP COLUMN card_language;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('015');
