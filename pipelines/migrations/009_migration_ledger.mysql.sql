-- Immutable applied-migration ledger. Runtime creates it before the first
-- migration so this file can itself be recorded in the same transaction.

CREATE TABLE IF NOT EXISTS cardz_migration_ledger (
    migration_file VARCHAR(255) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    applied_at DATETIME(6) NOT NULL,
    PRIMARY KEY (migration_file)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('009');
