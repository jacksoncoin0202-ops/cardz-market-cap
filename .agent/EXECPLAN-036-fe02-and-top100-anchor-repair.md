# FE02 presentation and reported Top 100 data repair

## Goal

Make the local 3800 FE02 review surface show the approved Market Cap branding
with an explicit Beta marker while retaining its direct Windows-3308 data
path.  Repair only the reported Top 100 data gaps from exact local/provider
evidence; do not import the unrelated static Vue beta prototype.

## Scope

- FE02 Next source in this worktree and its existing Market Cap logo assets.
- Reported global market ranks 5, 18, 49 and 99 only.
- Exact IDs already bound to those variants.
- No GitHub, staging, AWS, candidate-universe change, or broad collection.

## Progress

- [x] Identify the process serving 3800 and its source directory.
- [x] Separate the Market Cap direct-DB runtime from the static Vue beta prototype.
- [x] Identify the reported ranks and their accepted current/history evidence.
- [x] Apply the minimal FE02 presentation correction: the approved local logo is already present, and FE02 now carries its explicit Beta marker without replacing the direct-DB frontend.
- [x] Apply the reported exact history correction where exact local evidence exists.
- [x] Build and restart the same local FE02 artifact for review on 3800.
- [x] Complete the full six-adapter operator refresh, promote the passed 762-card snapshot, materialize public assets, rebuild FE02, and restart the standalone 3800 runtime.

## Decision log

- The static Vue beta prototype carries an old embedded Top 100 and is visual
  evidence only; replacing the direct-DB frontend with it would regress the
  current 762-card runtime.
- #5 and #99 have no accepted price point near their actual 30-day target.
  #18 and #49 do have exact PriceCharting 30-day anchors, so a missing display
  there is a frontend artifact/presentation issue rather than a DB gap.
- #5's exact SNK harvest returned zero PSA10 kline. Its one targeted
  PriceCharting collection did not yield a receipt before the existing CDP
  session timed out, so no value was written.
- #99's exact SNK binding has January and April PSA10 points only. The former
  PriceCharting POP Series 5 mapping is a different printing and stays
  quarantined instead of being used as a 30-day anchor.
- The FE02 standalone production artifact is now the listener on 127.0.0.1:3800
  with `CARDZ_DATA_MODE=live-db` and `CARDZ_REPO_ROOT` pinned to this checkout.
- The 2026-08-07 release generation is `product_subset_20260807T094818Z`; the
  live FE02 readback exposes DB generation `db3308_ab0b51aa013eb50b` with 762 cards.
