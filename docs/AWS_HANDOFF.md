# CARDZ backend: clean-clone and AWS handoff

This is the repeatable server handoff for the **backend only**. It does not deploy the website, publish a public snapshot, or run any third-party collector in GitHub Actions.

## Release-preparation gate

Run this in the repository that will be pushed. It is intentionally strict: the
approved canonical seed/archive must be both Git-tracked and materialized,
otherwise a clean clone cannot restore the canonical bootstrap. The archive is
canonical data, not a Top 100/300/350 storage lock; those are export views
derived after restore.

```powershell
git lfs install --local
git lfs pull --include="data/private/cardz-active-bootstrap.tar.gz"
python scripts/verify_handoff.py --require-archive --require-tracked --verify-archive
python scripts/backend.py registry --json
```

The command checks only file layout, ignore/LFS rules and the archive's lock/checksums. It never reads `.env`, opens a database connection, or prints credentials. A failure that says the archive is not Git-tracked means it must be included in the approved commit before a server handoff; do not claim clean-clone readiness until that gate passes.

## AWS/Linux installation

Use a private deploy identity and a Linux host with Python 3.10+ and Git LFS. Amazon Linux 2023 may need a versioned Python install because its default `python3` can be older than the required version. Do not replace the system Python symlink.

```bash
git clone <approved-private-repository-url> /opt/cardz-market-cap
cd /opt/cardz-market-cap
git lfs install --local
git lfs pull --include="data/private/cardz-active-bootstrap.tar.gz"
python3 scripts/verify_handoff.py --require-archive --require-tracked --verify-archive
python3 integrations/grade10/run_service.py self-check
python3 scripts/backend.py registry --json
```

For RDS, deliver `CARDZ_DB_*` and `CARDZ_DB_SSL_CA` through the host's approved secret manager into `/etc/cardz-market-cap/backend.env`; keep that file `root:root` and `0600`. The file is not copied from this workstation and never belongs in Git.

Install the repository's prepared systemd units and perform the one-time bootstrap, then enable the daily timer. The exact commands and ownership layout are in [the systemd runbook](../deploy/systemd/README.md). Both Windows Task Scheduler and Linux systemd execute the same `scripts/backend.py daily` entrypoint; no AI process is required for steady-state collection.

## Acceptance sequence

1. Verify the LFS archive, registry, and vendored Grade10 integration on a clean checkout.
2. Bootstrap an **empty** MySQL/RDS database; `seed-restore` never overwrites without its explicit flag.
3. Run `python3 scripts/backend.py status --json` and keep the output as an operator artifact.
4. Confirm `python3 scripts/backend.py explain market_cap` names the same authority, collector, database target, and test documented in the registry.
5. Let the 06:30 JST systemd timer finish two unattended daily runs; verify DB checkpoint progression, complete-rank recalculation, and that an injected source failure leaves last-good unchanged.
6. Only after these backend gates are met may a separately approved public-snapshot publisher be enabled for its requested presentation view.
