# CARDZ Market Cap Security and Public-Data Policy

## Security objective

CARDZ publishes useful market pages without publishing its collection system. A visitor may read the current ranking, a card page, localized editorial copy, and the bounded metrics rendered on that page. They must not receive a database dump, a provider-native identifier, an upstream URL, collection credentials, private history, or an unmasked slab asset.

No control can make public HTML impossible to copy. The practical objective is to remove bulk surfaces, keep upstream systems private, cache legitimate traffic, rate-limit abuse, and make automated extraction materially more expensive without breaking search discovery.

## Data classification

| Class | Examples | Permitted locations |
| --- | --- | --- |
| Public | Sanitized page data, opaque CARDZ IDs, QC-passed raw card fronts, localized stories | Versioned public generation, Worker-rendered HTML, pointer-authorized Worker media |
| Private | G10 full and incremental payloads, JLP records, source mappings, rejection evidence, full price and sale history, image review queues | Approved Windows runner, JLP, private R2 prefixes, private Git LFS archives |
| Secret | API keys, cookies, tokens, database credentials, signed URLs, authorization headers | 1Password and approved process memory only |

Private and secret values never belong in browser bundles, public R2 domains, HTML, URLs, telemetry, build traces, GitHub Actions, tickets, screenshots, documentation examples, or chat.

## Runtime boundary

```text
Private Windows runner -> JLP canonical data -> sanitized immutable generation
                                              -> private R2 bucket
                                              -> Worker server rendering
                                              -> bounded public HTML
```

- Collection and provider fallback run only on the private Windows runner.
- JLP MySQL 5.7 is the production canonical authority. A local SQLite replay is a fixture, not a production fallback.
- `MARKET_DATA` is a private R2 binding. Do not enable `r2.dev`, attach a public bucket domain, expose an object-listing endpoint, or return `latest.json` and generation objects directly.
- The Worker reads one validated pointer and one immutable generation server-side. A pointer mismatch, schema failure, or unavailable object fails closed to the previous in-memory or cached last-good page; it must not fall back to legacy/provider reads.
- Staging and production use different Worker names and different R2 buckets. Production rejects demo generations.

## Application controls

- `apps/web/next.config.ts` disables production browser source maps and Wrangler disables source-map upload.
- The global Content Security Policy permits only same-origin application, image, font, network, worker, and media resources. It contains no upstream/provider host.
- Framing, MIME sniffing, referrer leakage, browser capabilities, and insecure transport are restricted with response headers.
- API-shaped paths receive `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet` even when they return an error.
- Every deployment carries a sanitized `X-CARDZ-Build` value. Data-rendering responses carry `X-CARDZ-Generation`; neither value contains a provider ID.
- Public asset names are content-addressed. Cloudflare builds verify the local raw-front files and use a temporary allowlisted public directory, so neither market media nor local design artifacts enter the static asset bundle. The Worker requires each requested hash to appear in the active validated pointer before it reads the private R2 object; knowing an old R2 key is not authorization. Browser caching is five minutes and edge caching is one hour so a revoked asset does not remain indefinitely reachable through the Worker route. Top 100 imagery must be `raw_front`; unmasked slab files are forbidden from application public folders and build traces.

The static CSP retains `unsafe-inline` for Next.js bootstrap and styles. It deliberately omits `unsafe-eval` in production. Moving to a request nonce is a future hardening step and must be tested against the Cloudflare/OpenNext renderer before removing the compatibility directive.

## Automated leak gate

`npm run verify:public` scans the public snapshot, web source, and built output. It rejects:

- imports or paths that cross into `data/private`;
- provider names, provider-native IDs, provider hosts, and upstream URLs;
- secret assignments, bearer/JWT/GitHub/AWS credentials, private keys, and credential-like high-entropy tokens;
- `.map` files or source-map references in public build output;
- `slab_unmasked`, visible-cert, or visible-barcode markers;
- forbidden source/provider keys embedded anywhere in the snapshot;
- production Top 100 cards that fail identity, complete-number, population, freshness, localization, or raw-image gates;
- false zeroes for accumulating, stale, or unavailable metrics.

`--allow-demo` permits an honestly blocked local/staging generation while retaining all privacy and structural checks. It never makes that generation production eligible. `npm run verify:release` is the strict production gate: it also runs the official-registry dependency audit and requires every public image to be `human_or_vision_confirmed`.

## Cloudflare zone controls

Wrangler config owns the Worker and private R2 binding. The following zone-level controls are configured and verified separately in the Cloudflare dashboard or approved infrastructure-as-code; do not place zone tokens in this repository.

1. Enable managed WAF rules in log mode, review Security Events, then enforce.
2. Rate-limit unknown and expensive `/api/*` requests by IP and colo. Start with managed challenge before block to measure false positives.
3. Block requests for `/latest.json`, `/generations/*`, `/data/private/*`, source maps, and common secret filenames.
4. Enable the plan-appropriate bot product. Exempt verified search crawlers; do not treat a user-agent string as authentication.
5. If an authenticated CARDZ MCP or CLI is introduced, put it on a separate hostname with versioned schemas, per-client credentials, quotas, and API Shield validation. It must not reuse the website's internal R2 reader.

Bot score granularity and API Shield features vary by Cloudflare plan. The release record must state which controls are actually enabled; documentation alone is not proof.

## Hybrid SEO and GEO policy

Search visibility applies to editorial pages, not bulk market data.

| Surface | Google/Bing | OAI-SearchBot | Training crawlers | Public users |
| --- | --- | --- | --- | --- |
| Homepage, rankings, grader summaries, card stories | Allow | Allow | Disallow | Allow |
| `/api/*`, R2 pointers/generations, private history | Disallow and `noindex` | Disallow | Disallow | Deny |
| Staging | `noindex` or Cloudflare Access | `noindex` or Access | Deny | Approved testers only |

- Production `robots.txt` explicitly allows normal search discovery and disallows `/api/`, `/latest.json`, `/generations/`, and private paths.
- GPTBot and other training-only crawlers may be blocked while OAI-SearchBot remains allowed for search visibility.
- Canonical URL, `hreflang`, sitemap, Breadcrumb, ItemList, and Dataset markup contain only public facts. Do not invent offers, ratings, full-market sales coverage, or provenance claims.
- Robots directives are crawler preferences, not access control. Private surfaces must still return 401, 403, 404, or 405.

## Repository and CI rules

- The repository remains Private while any private archive exists in Git or LFS history. Changing visibility requires a new sanitized history.
- GitHub Actions fetches public LFS objects only and receives no collection or database credential.
- CI does not call third-party data sources and does not publish to Cloudflare.
- `.env*`, `.dev.vars*`, runtime G10 batches, logs, generated publish staging, and temporary masking work are ignored.
- Release and deployment remain separate: CI proves the artifact; an approved operator performs staging deploy and production cutover.

## Incident response

If a credential or private value may have escaped:

1. Stop the affected runner or route and prevent pointer advancement.
2. Revoke and rotate the credential in its provider and 1Password.
3. Roll the data pointer back to the previous verified immutable generation.
4. Inspect Git/LFS, CI artifacts, R2 objects, Worker versions, logs, screenshots, and chat without reproducing the secret.
5. Remove public access, rebuild Git history where required, and re-run source, build, and live canary scans.
6. Restore service only with new credentials and a verified clean generation.

Record generation ID, build ID, scope, containment time, and evidence hashes privately. Never record the exposed value itself.
