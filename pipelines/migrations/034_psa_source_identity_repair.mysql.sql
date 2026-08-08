-- PSA source identity repair. Names are literal PSA population-row descriptions;
-- provider-native evidence is mandatory for every current strict binding.

CREATE TABLE IF NOT EXISTS catalog_psa_identity_acceptance (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  variant_id BIGINT UNSIGNED NOT NULL,
  gemrate_id VARCHAR(191) NOT NULL,
  psa_description VARCHAR(500) NOT NULL,
  psa_year VARCHAR(16) NOT NULL,
  psa_set_name VARCHAR(255) NOT NULL,
  psa_card_number VARCHAR(96) NOT NULL,
  psa_parallel VARCHAR(96) NOT NULL DEFAULT '',
  psa_set_url VARCHAR(1000) NOT NULL DEFAULT '',
  psa_language VARCHAR(8) NOT NULL,
  raw_payload_sha256 CHAR(64) NOT NULL,
  psa_row_sha256 CHAR(64) NOT NULL,
  canonical_printing_sha256 CHAR(64) NOT NULL,
  evidence_sha256 CHAR(64) NOT NULL,
  lineage_sha256 CHAR(64) NOT NULL,
  source_observed_at DATETIME(6) NOT NULL,
  accepted_by VARCHAR(191) NOT NULL,
  accepted_at DATETIME(6) NOT NULL,
  supersedes_acceptance_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_catalog_psa_lineage (lineage_sha256),
  UNIQUE KEY uq_catalog_psa_raw_payload (raw_payload_sha256),
  UNIQUE KEY uq_catalog_psa_supersedes (supersedes_acceptance_id),
  KEY ix_catalog_psa_variant_current (variant_id,accepted_at),
  KEY ix_catalog_psa_gemrate (gemrate_id),
  CONSTRAINT fk_catalog_psa_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
  CONSTRAINT fk_catalog_psa_supersedes FOREIGN KEY (supersedes_acceptance_id) REFERENCES catalog_psa_identity_acceptance(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW operator_psa_identity_projection AS
SELECT a.*
FROM catalog_psa_identity_acceptance a
INNER JOIN catalog_printing_identity p
  ON p.variant_id=a.variant_id
 AND p.canonical_printing_sha256=a.canonical_printing_sha256
INNER JOIN catalog_source_identity si
  ON si.variant_id=a.variant_id
 AND si.source_code='gemrate'
 AND si.external_entity_id=a.gemrate_id
 AND LOWER(si.match_status)='exact'
 AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.type'))='provider_payload'
 AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.rawPayloadSha256'))=a.raw_payload_sha256
WHERE a.psa_description<>''
  AND a.raw_payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.psa_row_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM catalog_psa_identity_acceptance newer
    WHERE newer.supersedes_acceptance_id=a.id
  );

CREATE OR REPLACE VIEW operator_strict_source_identity AS
SELECT si.*
FROM catalog_source_identity si
INNER JOIN catalog_printing_identity p ON p.variant_id=si.variant_id
WHERE LOWER(si.match_status)='exact'
  AND TRIM(si.source_product_number)<>''
  AND LOWER(TRIM(si.bound_tcg_code))=LOWER(TRIM(p.tcg_code))
  AND LOWER(TRIM(si.bound_card_language))=LOWER(TRIM(p.card_language))
  AND LOWER(TRIM(si.bound_collector_number))=LOWER(TRIM(p.collector_number))
  AND LOWER(TRIM(si.bound_set_code))=LOWER(TRIM(p.set_code))
  AND LOWER(TRIM(si.bound_printing_code))=LOWER(TRIM(p.printing_code))
  AND (LOWER(TRIM(p.edition_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_edition_code))=LOWER(TRIM(p.edition_code)))
  AND (LOWER(TRIM(p.parallel_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_parallel_code))=LOWER(TRIM(p.parallel_code)))
  AND (LOWER(TRIM(p.finish_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_finish_code))=LOWER(TRIM(p.finish_code)))
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.type'))='provider_payload'
  AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.sha256')) REGEXP '^[0-9a-f]{64}$'
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.tcgCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.cardLanguage') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.collectorNumber') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.setCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.printingCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.parallelCode') IS NOT NULL
  AND (
    si.source_code<>'gemrate'
    OR EXISTS (
      SELECT 1 FROM operator_psa_identity_projection psa
      WHERE psa.variant_id=si.variant_id
        AND psa.gemrate_id=si.external_entity_id
        AND psa.raw_payload_sha256=JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.rawPayloadSha256'))
    )
  );

CREATE OR REPLACE VIEW operator_official_name_projection AS
SELECT
  a.variant_id,
  a.id AS official_name_acceptance_id,
  a.psa_description AS official_full_name,
  'gemrate' AS official_name_source_code,
  a.gemrate_id AS official_name_external_entity_id,
  a.evidence_sha256 AS official_name_evidence_sha256,
  a.raw_payload_sha256 AS official_name_payload_sha256,
  a.source_observed_at AS official_name_observed_at,
  a.lineage_sha256 AS official_name_lineage_sha256
FROM operator_psa_identity_projection a;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('034');
