# AWS Deployment Guide — CARDZ Market Cap Web

**Audience:** the third party deploying this on AWS. You do **not** need Cloudflare, and you do **not** need a database.

**Last verified:** 2026-07-26, by real `docker build` + `docker run` + `curl` on this repo. Every number below came from an actual run, not an estimate.

---

## 1. TL;DR

`apps/web` is a Next.js 16 app that was originally built for Cloudflare Workers. It now has a **second, independent deployment path** that produces a plain Node.js server — that is the path you want.

```bash
# from the REPOSITORY ROOT (not apps/web)
docker build -f apps/web/Dockerfile -t cardz-web:latest .
docker run -d -p 3000:3000 cardz-web:latest
curl http://localhost:3000/api/health
```

- **No database.** All market data is baked into the image at build time.
- **No Cloudflare.** The runtime image contains zero wrangler / workerd / OpenNext code (verified).
- **No S3 / R2 required.** Card images are served as static files from inside the image.
- **Stateless.** Scale horizontally, no shared storage, no sticky sessions.

Health check endpoint: **`GET /api/health`** — see §6, and read the warning there.

---

## 2. Where the data comes from (important — read this)

Market data is **build-time**, not runtime. `apps/web/src/lib/snapshot.ts` statically imports the JSON, so it becomes part of the compiled bundle.

The build reads exactly three paths from the repo root:

| Path | Size | Purpose |
|---|---|---|
| `data/public/seed-snapshot.json` | ~4.2 MB | The market snapshot (cards, prices, populations, rankings) |
| `data/public/market-assets/` | ~104 MB | Content-addressed card images (`<sha256>.webp` plus `_200` / `_600` derivatives) |
| `data/editorial/top100-stories.json` | ~96 KB | Editorial copy for card detail pages |

During `npm run build`, the `prebuild` step runs `apps/web/scripts/sync-snapshot.mjs`, which:
1. Requires `schemaVersion === "2.0.0"`.
2. Verifies every card image reference matches `/^\/market-assets\/([a-f0-9]{64})\.webp$/`.
3. **Recomputes the SHA-256 of every referenced file on disk and fails the build if it does not match the filename.**
4. Copies the referenced assets (plus derivatives) into `apps/web/public/market-assets/`.

That means a corrupt or mismatched image set fails the build rather than producing a broken site. In the verified run it reported `Synced 251 referenced raw_front assets`.

**Consequence for you:** there are **two** supported ways to ship data, and you should know both.

| | How | When to use |
|---|---|---|
| **A. Runtime override** *(recommended for daily updates)* | Mount a snapshot file into the container and set `MARKET_DATA_SNAPSHOT_PATH` to it. Restart to pick up new data. | Data changes daily; image does not. No rebuild, no redeploy pipeline. |
| **B. Rebuild** | Replace `data/public/seed-snapshot.json` in the **build context** (never commit it) and rebuild the image. | You want one immutable artifact per data generation. |

Path A was verified working on **2026-07-26** (`docker run -v <dir>:/snap:ro -e MARKET_DATA_SNAPSHOT_PATH=/snap/demo.json` → `/api/health` **200**). An earlier revision of this document claimed the variable was broken; that conclusion was drawn from a snapshot file that no longer exists — see §8.3.

Whichever you pick, read **§6.1 "Shipping real data"** — the container refuses to serve non-production data unless you opt in, and that refusal is deliberate.

---

## 3. Build with Docker (recommended)

The Dockerfile is at `apps/web/Dockerfile`, but **the build context must be the repository root**, because this is an npm workspaces monorepo and the build needs `packages/market-data` and `data/`.

```bash
cd /path/to/cardz-market-cap

docker build \
  -f apps/web/Dockerfile \
  --build-arg CARDZ_PUBLIC_BUILD_ID=rel-20260726 \
  -t cardz-web:latest \
  .
```

| Fact | Verified value |
|---|---|
| Final image size | **638 MB** |
| Build stages | `deps` (npm ci) → `builder` (next build) → `runner` |
| Runs as | non-root user `node` |
| Exposed port | `3000` |
| Entrypoint | `node apps/web/server.js` |

A root `.dockerignore` restricts the build context to only what is needed. This matters: the repo's `data/` directory is ~6.8 GB and the root contains `.env.private`. The `.dockerignore` is deny-all-then-allow, so **secrets and bulk data never enter the build context**.

### Push to ECR

```bash
AWS_REGION=ap-northeast-1
ACCOUNT_ID=<your-account-id>
REPO=cardz-web

aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

aws ecr create-repository --repository-name $REPO --region $AWS_REGION   # first time only

docker tag cardz-web:latest $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest
docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest
```

> Build on `linux/amd64` if your ECS tasks are x86. On an ARM Mac add `--platform linux/amd64` to `docker build`.

---

## 4. Build without Docker (plain EC2)

If you would rather run it directly on an EC2 instance with Node 22 or 24:

```bash
cd /path/to/cardz-market-cap
npm ci

cd apps/web
CARDZ_BUILD_TARGET=node CARDZ_PUBLIC_BUILD_ID=rel-20260726 npm run build

# Next's standalone output does NOT include static assets or public/. Copy them in:
cp -r .next/static  .next/standalone/apps/web/.next/static
cp -r public        .next/standalone/apps/web/public
```

Artifacts land at `apps/web/.next/standalone/`:

```
apps/web/.next/standalone/
├── node_modules/            # pruned production deps
└── apps/web/
    ├── server.js            # <- entrypoint
    ├── .next/
    │   ├── static/          # copied in manually above
    │   └── server/
    └── public/              # copied in manually above
```

Run it:

```bash
cd apps/web/.next/standalone
CARDZ_RUNTIME=node NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 \
  node apps/web/server.js
```

`CARDZ_BUILD_TARGET=node` is what switches `next.config.ts` to `output: "standalone"`. Without it you get a normal build that has no `server.js` and cannot run this way. The Cloudflare path never sets it, which is how the two paths coexist.

systemd unit for EC2:

```ini
[Unit]
Description=CARDZ Market Cap Web
After=network.target

[Service]
Type=simple
User=cardz
WorkingDirectory=/opt/cardz/standalone
Environment=NODE_ENV=production
Environment=CARDZ_RUNTIME=node
Environment=PORT=3000
Environment=HOSTNAME=0.0.0.0
ExecStart=/usr/bin/node apps/web/server.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 5. Environment variables

### Runtime

| Variable | Required | Default | What it does |
|---|---|---|---|
| `CARDZ_RUNTIME` | **Yes** | baked to `node` in the image | **Must be `node`.** Selects the Node data path. See the warning below. |
| `NODE_ENV` | Yes | baked to `production` | Standard. Anything other than `production` changes data loading. |
| `PORT` | No | `3000` | Listening port. |
| `HOSTNAME` | No | `0.0.0.0` | Must be `0.0.0.0` in a container, otherwise the port is unreachable. |
| `CARDZ_ENVIRONMENT` | No | unset | If set to `canary` or `staging`, `/robots.txt` returns disallow-all. **Leave unset for production.** |
| `MARKET_DATA_SNAPSHOT_PATH` | No | unset | Absolute path to a snapshot JSON to load at runtime instead of the baked-in one. **Works** (verified 2026-07-26). The file is validated with `assertPublicSnapshot()` before it is served; if it fails, `/api/health` returns 503 and the reason lists every error. Read once and cached per process — **restart the container to pick up new data.** |
| `MARKET_DATA_ALLOW_DEMO` | No | unset | `true` permits serving a snapshot that is **not** production-eligible. Without it the container **refuses to start serving** non-production data (503) rather than silently publishing demo cards. See §6.1. |

> ### ⚠️ `CARDZ_RUNTIME=node` is not optional
> If it is missing, `apps/web/src/lib/cloudflare-env.ts` falls through to `getCloudflareContext()`, which **dynamically imports `wrangler` and boots a workerd/miniflare subprocess** that reads `wrangler.jsonc`. On a normal Node server that is wrong and will fail or hang. It is baked into the Docker image via `ENV CARDZ_RUNTIME=node`; do not override it. If you deploy the non-Docker path (§4), you must set it yourself.

### Build time

| Variable | Default | What it does |
|---|---|---|
| `CARDZ_BUILD_TARGET` | unset | Set to `node` to emit `.next/standalone`. The Dockerfile sets this. |
| `CARDZ_PUBLIC_BUILD_ID` | `docker` (Dockerfile ARG) | Becomes the Next build ID and the `X-CARDZ-Build` response header. Must match `^[A-Za-z0-9._-]{1,64}$` or it silently becomes `invalid-build-id`. |
| `NEXT_PUBLIC_SITE_URL` | `https://cardzmarketcap.com` | Canonical origin. Used by `metadataBase`, `robots.txt`, `sitemap.xml` and JSON-LD structured data. |

> **`NEXT_PUBLIC_SITE_URL` is inlined at build time.** Next.js substitutes `NEXT_PUBLIC_*` during compilation, so setting it at runtime has no effect. If you serve from a different domain, pass it as a build arg and rebuild:
> ```bash
> docker build -f apps/web/Dockerfile \
>   --build-arg CARDZ_PUBLIC_BUILD_ID=rel-20260726 \
>   -t cardz-web:latest .
> ```
> (To wire it through, add `ARG NEXT_PUBLIC_SITE_URL` / `ENV NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL}` to the `builder` stage — it is not currently parameterised, so the default domain is compiled in.)

---

## 6. Health check

**Endpoint:** `GET /api/health`

Success (HTTP 200) — this is the shape, with real values from a run on **2026-07-26**:

```json
{
  "status": "ok",
  "generation": "daily_20260722T094826496874Z",
  "effectiveAt": "2026-07-22T09:48:26.496874Z",
  "mode": "preview",
  "cards": 360
}
```

### 🔴 `mode` is the field that tells you whether real data is live

| `mode` | Meaning |
|---|---|
| `"canonical"` | Real market data. `generation.mode === "production"` **and** `productionEligible === true`. |
| `"preview"` | **Demo / placeholder data.** Not real. |

The sample above says `"preview"` because the snapshot committed to git is a demo placeholder **by design** — git must never carry production data. **A 200 response does not mean you are serving real data.** Alert on `mode != "canonical"`, not just on the status code.

Failure (HTTP 503):

```json
{ "status": "error", "reason": "<why the snapshot could not be loaded>" }
```

The image also ships a Docker `HEALTHCHECK` hitting the same endpoint (`interval=30s`, `timeout=5s`, `start-period=15s`, `retries=3`). Verified: container reports `healthy`.

### ⚠️ Do not health-check `/`

The root path has a `loading.tsx` and no error boundary, so Next.js streams the HTML shell and flushes **HTTP 200 before** rendering finishes. If snapshot loading fails, `/` still returns **200** with a broken body. This was reproduced deliberately: with a bad snapshot, `/api/health` returned 503 while `/` returned 200.

**Your ALB / ECS target group health check path must be `/api/health`.** Suggested settings: healthy threshold 2, unhealthy threshold 3, timeout 5s, interval 30s, success codes 200.

Cold start is fast — in the verified run the container answered health 200 **one second** after `docker run`. A 15–30s `start-period` / grace period is plenty.

---

## 6.1 Shipping real data

> This is the step that was missing from earlier revisions of this document. Following the old
> instructions produced a container serving **demo cards to real visitors** with HTTP 200, no error,
> no log line and no alert. Read this section before you go live.

### The default is demo, and that is on purpose

`data/public/seed-snapshot.json` in git is a **demo placeholder**, permanently. Production data must
never be committed. So a clean `git clone` → `docker build` → `docker run` gives you a working site
serving **360 fake cards**. Shipping real data is a deliberate extra step; it does not happen by itself.

### Step 1 — produce a production snapshot

```bash
# ⚠️ --output DEFAULTS TO data/public/seed-snapshot.json.
#    Running this bare OVERWRITES the demo seed in your working tree. Always pass --output.
python -X utf8 pipelines/canonical_public_snapshot.py \
  --production \
  --output temp/prod-snapshot.json
```

Confirm what you produced before shipping it:

```bash
python -X utf8 -c "import json;g=json.load(open('temp/prod-snapshot.json',encoding='utf-8'))['generation'];print(g['id'],g['mode'],g['productionEligible'],g.get('blockers'))"
```

You need `mode = production`, `productionEligible = True`, and an **empty** `blockers` list. If
blockers remain, the snapshot is not shippable — that is an upstream data problem, not a deploy
problem, and the container will (correctly) refuse it.

### Step 2 — ship it

**Path A — runtime override (no rebuild):**

```bash
docker run -d -p 3000:3000 \
  -v /srv/cardz/snapshots:/snap:ro \
  -e MARKET_DATA_SNAPSHOT_PATH=/snap/prod-snapshot.json \
  cardz-web:latest
```

The snapshot is read **once per process** and cached. To publish new data: replace the file, then
restart the container. No rebuild.

**Path B — rebuild:** copy the production snapshot over `data/public/seed-snapshot.json` **in the
build context only**, then `docker build`. Never `git add` that file.

### Step 3 — verify you are actually serving real data

```bash
curl -s http://127.0.0.1:3000/api/health
```

Check all three:

| Check | Pass |
|---|---|
| HTTP status | `200` |
| `mode` | **`"canonical"`** — if it says `"preview"` you are serving demo data |
| `generation` | matches the `id` you produced in Step 1 |

### The refusal is a feature

If the snapshot is not production-eligible, `/api/health` returns **503** and the `reason` field
lists every validation error. That is intentional: an unreachable site is recoverable, a site
quietly serving fake market data to real users is not.

To serve demo data on purpose (staging, a UI demo, a smoke test), opt in explicitly:

```bash
docker run -d -p 3000:3000 -e MARKET_DATA_ALLOW_DEMO=true cardz-web:latest
```

Measured 2026-07-26 on `cardz-web` built from this repo:

| Configuration | `/api/health` |
|---|---|
| no env vars (demo seed baked in) | **503** — refuses to serve non-production data |
| `MARKET_DATA_ALLOW_DEMO=true` | **200** (`mode: preview`) |
| `MARKET_DATA_SNAPSHOT_PATH` → demo file | **503** — fails production validation |
| `MARKET_DATA_SNAPSHOT_PATH` → demo file + `MARKET_DATA_ALLOW_DEMO=true` | **200** — proves the runtime override path works |

---

## 7. Verified test results

All of the following were executed against the actual container (`docker run -p 3990:3000 cardz-web:latest`) on 2026-07-26.

| Route | Status | Response size |
|---|---|---|
| `/api/health` | 200 | JSON above |
| `/` | 200 | 1,061,847 B |
| `/pokemon` | 200 | 1,067,716 B |
| `/one-piece` | 200 | 256,725 B |
| `/watchlist` | 200 | 1,648,599 B |
| `/graders/psa` | 200 | 841,955 B |
| `/tune` | 200 | 613,668 B |
| `/card/cmc_4104320a31c4d989742c067d` | 200 | 62,519 B |
| `/api/v1/market?scope=all` | 200 | 520,586 B |
| `/api/v1/cards/<id>` | 200 | 20,278 B |
| `/api/v1/graders/psa` | 200 | 515,208 B |
| `/robots.txt` | 200 | 376 B |
| `/sitemap.xml` | 200 | 221,010 B |
| `/market-assets/<sha256>.webp` | 200 | 307,718 B, `image/webp` |
| `/market-assets/<unknown>.webp` | 404 | — |

**Content is real, not a blank page.** The homepage HTML contains 101 `<tr>` rows, 900 `market-assets` image references, `<h1>Top 100 market heatmap</h1>`, and the strings `Charizard` and `PSA`. The card detail page renders `<h1>Pikachu with Grey Felt Hat</h1>`. Neither page contains a React error digest.

**Other checks that passed:**
- Middleware language negotiation: `?lang=ja` → `<html lang="ja">`, `?lang=zh-TW` → `<html lang="zh-Hant">`, `?lang=ko` → `<html lang="ko">`.
- Security headers present on `/`: `Content-Security-Policy`, `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and `X-CARDZ-Build: aws-verify` (proving the build arg flowed through).
- **No Cloudflare code in the runtime image:** `ls node_modules | grep -iE "wrangler|workerd|opennext"` returned nothing.
- Idle memory: **182.6 MiB**. Suggested ECS task: 0.5 vCPU / 1024 MB.
- Docker's own `HEALTHCHECK` reaches `healthy` within ~10 s of start.

### What was tested and *failed* (so you don't repeat it)

`MARKET_DATA_SNAPSHOT_PATH` was tested twice against the repo snapshot, mounted read-only. Both runs failed:

| Run | `/api/health` | `/` | `/api/v1/market` |
|---|---|---|---|
| `MARKET_DATA_SNAPSHOT_PATH` only | **503** (708 errors) | 200, 22,662 B, **1 React error digest, 0 images** | **500** |
| `+ MARKET_DATA_ALLOW_DEMO=true` | **503** (6 errors) | 200, 22,662 B, broken body | **500** |

This is the §8.3 defect, and it is also a live demonstration of the `/`-returns-200-while-broken behaviour described below: a 22 KB body with an error digest instead of the healthy 1,061,847 B. **Health-check `/api/health`, never `/`.**

---

## 8. Known limitations

1. **There is no runtime DB connection.** Data arrives as a snapshot file, either baked into the image at build time or mounted and pointed to by `MARKET_DATA_SNAPSHOT_PATH` (§2, §6.1). Either way it is read **once per process and cached**, so publishing new data always requires a container restart. *(Corrected 2026-07-26: this item previously said data was frozen at build time and rebuilding was the only option. That was wrong — see §8.3.)*

2. **`/` returns HTTP 200 even when data loading fails** (streaming SSR, see §6). Use `/api/health` for health checks, and do not infer health from the homepage returning 200.

3. **`MARKET_DATA_SNAPSHOT_PATH` works. The claim below that it was broken has been retracted.**

   **Re-measured 2026-07-26** (`docker run -v <dir>:/snap:ro -e MARKET_DATA_SNAPSHOT_PATH=/snap/<file>`, image built from this repo):

   | Snapshot pointed at | `MARKET_DATA_ALLOW_DEMO` | `/api/health` | Meaning |
   |---|---|---|---|
   | demo seed (`daily_20260722T094826496874Z`) | unset | 503 | correct — demo rejected under production validation |
   | demo seed | `true` | **200** | **the runtime override mechanism works end-to-end** |
   | production-*flagged* copy of the demo seed | unset | 503 | rejected on **data quality**, not on mechanism (see below) |

   The third row is worth reading carefully. To test whether a production snapshot loads, a copy of
   the demo seed was flipped to `mode: production` / `productionEligible: true` with blockers
   cleared. It was still rejected, with errors of exactly two kinds:
   `generation content hash is inconsistent` (self-inflicted — editing the JSON invalidated
   `contentSha256`) and per-card data-quality failures (`identity is not confirmed`,
   `localization is incomplete`, `stories are not independently localized`,
   `price exceeds 48h freshness SLA`).

   **None of those are deployment failures.** They are the known upstream data-readiness blockers.
   The loader, the validator and the HTTP path all behave correctly. What could **not** be verified
   is an end-to-end load of a genuinely production-eligible snapshot, because **no such snapshot
   exists in this repo yet** — see `docs/DATA_GAPS.md`.

   <details>
   <summary>Retracted: the original 2026-07-24 measurement (kept for provenance)</summary>

   Pointed at `data/public/seed-snapshot.json` = `canonical_20260724_0a295bbce68a`,
   `assertPublicSnapshot()` rejected it and `/api/health` returned 503:

   | Mode | Errors | Breakdown |
   |---|---:|---|
   | Default (`production: true`) | **708** | 302 `watchlist[N] identity localization is incomplete` · 300 `top100[N] localization is incomplete` · 100 `top100[N] stories are not independently localized` · 6 `subset collector number is missing its denominator` |
   | `MARKET_DATA_ALLOW_DEMO=true` | **6** | 6 `subset collector number is missing its denominator` |

   **`MARKET_DATA_ALLOW_DEMO=true` is not a workaround.** It only skips the `options.production` block inside [`assertPublicSnapshot()`](../packages/market-data/src/validate.ts); the collector-number assertion (`subset collector number is missing its denominator`, inside `validateCard()`) fires in every mode. The 6 offending cards are subset numbers with no denominator — `GG69`, `GG44`, `SV49`, `SV107`, `GG70`, `TG20` (`top100` indices 35, 37, 41, 59, 82, 83).

   The 702 localization errors are the pre-existing i18n blocker already tracked in `CLAUDE.md`; they are not caused by this deployment work.

   > ⚠️ **The file this section measured no longer exists.** Everything above was measured on
   > **2026-07-24** against `data/public/seed-snapshot.json` = `canonical_20260724_0a295bbce68a`.
   > That copy was production data committed into the demo seed — a violation of the hard rule in
   > `CLAUDE.md` — and it has since been reverted. Re-measured **2026-07-26**: the working-tree seed
   > is `daily_20260722T094826496874Z`, and the six offending cards are **gone** (cards with an
   > incomplete `collectorNumber` = **0**). The 503 above is therefore **not reproducible on today's
   > seed**, and the conclusion "rebuild is the only way to ship data" does **not** follow from it.
   > Re-measure before relying on this section:
   > ```bash
   > python -X utf8 -c "import json;d=json.load(open('data/public/seed-snapshot.json',encoding='utf-8'));print(d['generation']['id'])"
   > ```
   > Only the *object identity* recorded above (`canonical_20260724_…`) made this detectable. A
   > measurement stamped with a date but no object identity would have looked permanently valid.

   </details>

   **What to actually do:** follow §6.1. Either path (runtime override or rebuild) is supported; the
   thing that decides whether you serve real data is the **snapshot's own** `mode` /
   `productionEligible` / `blockers`, not which shipping path you pick.

   ⚠️ Not AWS-specific: Cloudflare calls the same `assertPublicSnapshot()` on the R2 object, so a
   snapshot that fails here fails there too.

4. **Localisation is English-only.** Japanese / Chinese / Korean card names and stories are not yet populated. The language switcher works, but non-English locales fall back to English text.

5. **`NEXT_PUBLIC_SITE_URL` is compiled in** (§5). The default `https://cardzmarketcap.com` is baked unless you parameterise the Dockerfile.

6. **A fresh `git clone` will not reproduce this build.** All 251 card images referenced by the current snapshot are **untracked** in git, and the committed `seed-snapshot.json` is an older revision. Hand over either the built image or a file copy of the working tree — not a clone. (Verified: referenced-and-tracked = 0 of 251.)

7. **The image is 638 MB**, mostly the ~81 MB of card images plus Node dependencies. Fine for ECS; be aware of ECR storage and pull time.

8. **`build:cloudflare` cannot be run on this Windows machine** (irrelevant to AWS, noted so you do not chase it). A stale, locked `apps/web/.open-next/assets` directory from 2026-07-24 makes OpenNext fail with `EPERM` at `initOutputDir`, before any application code compiles. OpenNext itself warns it is not fully Windows-compatible and recommends WSL. **The Cloudflare build was verified to still succeed on Linux** — see §9.

9. Next.js 16 prints a deprecation warning that the `middleware` file convention should become `proxy`. Cosmetic; the middleware works.

---

## 9. The Cloudflare path is untouched

Both deployment paths coexist. Nothing in this guide changes Cloudflare behaviour.

| | Cloudflare Workers | AWS / Node |
|---|---|---|
| Build | `npm run build:cloudflare` | `CARDZ_BUILD_TARGET=node npm run build` |
| Output | `.open-next/worker.js` | `.next/standalone/apps/web/server.js` |
| Data source | R2 bucket via `MARKET_DATA` binding, `latest.json` pointer | Baked-in snapshot (or `MARKET_DATA_SNAPSHOT_PATH`) |
| Config | `wrangler.jsonc`, `open-next.config.ts` | `apps/web/Dockerfile` |
| Runtime switch | `CARDZ_RUNTIME` unset | `CARDZ_RUNTIME=node` |

The separation lives in `apps/web/src/lib/cloudflare-env.ts`: under `CARDZ_RUNTIME=node` it returns `null` without importing `@opennextjs/cloudflare` at all; otherwise it behaves exactly as before. `output: "standalone"` is likewise gated behind `CARDZ_BUILD_TARGET=node`, so the Cloudflare build produces the same artifact it always did.

**Verified, not assumed.** `npm run build:cloudflare` was executed on Linux against this exact working tree and exited 0:

```
Verified 251 raw_front assets without bundling them into Cloudflare static assets.
✓ Compiled successfully
Bundling middleware function...
⚙️ Bundling the OpenNext server...
Worker saved in `.open-next/worker.js` 🚀
OpenNext build complete.
```

`apps/web/public/` was correctly restored afterwards by `cloudflare-build.mjs`.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Container starts then exits, or hangs on first request | `CARDZ_RUNTIME` not set to `node` | Set it; see §5 warning |
| Port unreachable from outside the container | `HOSTNAME` not `0.0.0.0` | Set `HOSTNAME=0.0.0.0` |
| Build fails: `Module not found: .../data/editorial/top100-stories.json` | Build context missing `data/` | Build from repo root, keep the root `.dockerignore` |
| Build fails in `sync-snapshot.mjs` with a hash mismatch | `data/public/market-assets/` is incomplete or corrupt | Restore the full asset directory; the check is intentional |
| `/api/health` 503, reason mentions `localization` or `denominator` | You set `MARKET_DATA_SNAPSHOT_PATH` | **Unset it.** `MARKET_DATA_ALLOW_DEMO=true` does *not* fix this — see §8.3 |
| Site loads but all pages show stale data after a data update | Snapshot is cached per process | Restart / redeploy the container |
| `/robots.txt` disallows everything | `CARDZ_ENVIRONMENT` is `canary` or `staging` | Unset it in production |
