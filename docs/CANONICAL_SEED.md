# Canonical MySQL logical seed

`scripts/canonical_seed.py` produces the portable data handoff after a full
canonical import has passed. It emits two detached files:

- `canonical-seed.sql.gz`: normalized data-only SQL for a MySQL database whose
  repository migrations have already been applied.
- `canonical-seed.manifest.json`: SHA-256 checksums, canonical-table row
  counts, exact migration-file SHA-256 ledger, and the explicit raw-payload
  exclusion contract.

The seed includes the migration ledger, canonical identity, ingest checkpoint,
population, price, sales, FX, membership, ranking, alert, image-QC, and
story-pointer records. It
does not include `market_source_observation.payload_json`, provider raw-object
retention tables, credentials, cookies, or raw landing payloads. The immutable
private landing archive remains the source of record for those excluded rows.

Build only after the database has migrated and the full import has completed:

```powershell
$env:CARDZ_DB_HOST = '127.0.0.1'
$env:CARDZ_DB_PORT = '3306'
$env:CARDZ_DB_USER = 'cardz'
$env:CARDZ_DB_NAME = 'cardz_market_cap'
# Inject CARDZ_DB_PASSWORD through the process or the platform secret store.
python scripts/canonical_seed.py build `
  --output data/private/canonical-seed.sql.gz
```

Verify it before handing it to another operator:

```powershell
python scripts/canonical_seed.py verify `
  --seed data/private/canonical-seed.sql.gz
```

The builder fails closed when a required canonical table is missing, a secret
pattern occurs in output, the detached manifest exists unexpectedly, or the
database does not expose all migrations. It never modifies the database. A
later `seed-restore` step must accept only an empty migrated database and verify
this detached manifest before applying the SQL.
