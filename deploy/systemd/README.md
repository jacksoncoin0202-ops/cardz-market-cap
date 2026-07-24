# AWS/Linux systemd deployment

These units assume the private repository is installed at `/opt/cardz-market-cap`, runs as the locked-down `cardz` user, and connects to an external MySQL-compatible database such as Amazon RDS. Secrets come from `/etc/cardz-market-cap/backend.env` with mode `0600`. Change all three values together if the server layout differs.

Install Python 3.10 or newer, Git, and Git LFS using the package manager for the chosen Linux distribution. Clone the private repository through an approved deploy identity, then materialize the private bootstrap LFS object:

```bash
if ! id -u cardz >/dev/null 2>&1; then
  sudo useradd --system --home-dir /opt/cardz-market-cap --shell /usr/sbin/nologin cardz
fi
cd /opt/cardz-market-cap
git lfs install --local
git lfs pull --include="data/private/cardz-active-bootstrap.tar.gz"
sudo install -d -m 0750 -o cardz -g cardz /etc/cardz-market-cap
sudo install -d -m 0750 -o cardz -g cardz data/runtime data/private/gemrate integrations/grade10/data .venv-backend
```

Materialize `/etc/cardz-market-cap/backend.env` from the approved server secret manager. It must contain `CARDZ_DB_MODE=external`, the five `CARDZ_DB_*` connection values, and a readable `CARDZ_DB_SSL_CA` path. Keep it owned by `root:root` with mode `0600`; do not put that file in Git. Install the units and bootstrap once before enabling the timer:

```bash
sudo install -m 0644 deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start cardz-market-cap-bootstrap.service
sudo systemctl status cardz-market-cap-bootstrap.service
```

Install and start the scheduled service:

```bash
sudo systemctl enable --now cardz-market-cap-daily.timer
sudo systemctl start cardz-market-cap-daily.service
sudo systemctl status cardz-market-cap-daily.service cardz-market-cap-daily.timer
```

The 06:30 parent job first refreshes the broad constituent radar, then collects the frozen active universe, appends/imports canonical observations, and derives rankings and alerts. Any required-stage failure prevents later stages from running and preserves the last-good database/public generation. `cardz-grade10-discovery.service` and its timer remain optional operator preflight tools; they are not required because the parent daily job performs the same discovery gate. Raw acquisition output and database credentials never enter Git.

After installation, let the timer complete at least two consecutive unattended runs before production cutover. Verify with `systemctl list-timers` and `journalctl -u cardz-market-cap-daily.service`; inspect status/counts only and never print the environment file.
