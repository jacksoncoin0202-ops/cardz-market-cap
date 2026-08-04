# CARDZ Market Cap One-time 0→1（WSL）

## 今晚鎖定範圍

- Active product cohort：762 張 freeze-ready cards。
- Candidate backlog：776 張 POP >= 1000 cards，今次唔入 snapshot。
- Product：Top 100 Market Cap + 101–762 watchlist。
- Frontend：冇 `Graders` nav/route、`Grading Pulse`、grader share、card-detail multi-grader panel。

## 邊一個位先係 SNK 圖正解

**Step 2 `daily --... --pass` 係唯一 authoritative image-decision point。**

原因：pass 會先用當刻最新 price × PSA10 POP 重新排實際顯示 Top 100，再逐卡套用 `displayed-top100-snk-public-exact-first-v1`：

1. 有 exact pointer + public-approved + raw-front confirmed SNK asset：必須揀 SNK。
2. 冇合資格 SNK asset：保留原 accepted freeze image。
3. Rank 101+：沿用 accepted freeze policy，唔受 Top-100 override 影響。
4. `snk-image-priority-status` 只係 Step 1 盤點；唔係 publication authority。
5. Promotion、validator 只接受同 pass receipt 完全一致嘅 per-card decision/hash。

## 唯一流程

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
PY=/home/jackson0202/cardz-market-cap/.venv-backend/bin/python
RELEASE=/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap-release-20260804

# 1. SNK displayed-Top-100 inventory（read-only，一次）
"$PY" -X utf8 pipelines/operator_control.py snk-image-priority-status

# 2A. 正常正式 refresh + pass（一次）
"$PY" -X utf8 pipelines/operator_control.py daily --refresh --pass

# 2B. 只在已完成 refresh、獲批准重生 presentation pass 時取代 2A；唔可以兩條都跑
"$PY" -X utf8 pipelines/operator_control.py daily \
  --refresh-report data/runtime/operator/pass_receipt.json --pass

# Step 2 先做 production frontend preflight；generated/cache/secret/retired-grader surface 會即刻 fail

# 3. Promotion（一次）
"$PY" -X utf8 pipelines/operator_control.py promote-product-subset \
  --snapshot data/runtime/operator/product-subset-snapshot.json \
  --receipt data/runtime/operator/pass_receipt.json \
  --output data/runtime/operator/promoted-product-snapshot.json \
  --expected-cards 762 --expected-backlog 776

# 4. Materialize exact image triplets（一次）
"$PY" -X utf8 scripts/materialize_snapshot_assets.py \
  --snapshot data/runtime/operator/promoted-product-snapshot.json \
  --assets data/public/market-assets \
  --output-snapshot data/runtime/operator/materialized-product-snapshot.json \
  --receipt data/runtime/operator/asset-materialization-receipt.json

# 5. Bake current source frontend + runtime + snapshot assets（一次）
"$PY" -X utf8 scripts/bake_tonight_release.py \
  --snapshot data/runtime/operator/materialized-product-snapshot.json \
  --source-assets data/public/market-assets \
  --release-root "$RELEASE" \
  --frontend-receipt data/runtime/operator/frontend-bundle-receipt.json \
  --expected-cards 762

# 6. One release validator（一次）
"$PY" -X utf8 scripts/validate_tonight_release.py \
  --snapshot data/runtime/operator/materialized-product-snapshot.json \
  --receipt data/runtime/operator/pass_receipt.json \
  --assets-root "$RELEASE/data/public/market-assets" \
  --release-root "$RELEASE" \
  --frontend-receipt data/runtime/operator/frontend-bundle-receipt.json \
  --asset-materialization-receipt data/runtime/operator/asset-materialization-receipt.json \
  --expected-cards 762 --expected-backlog 776
```

跟住去 clean release root 只 build 一次、3800 health 一次、DADDY visual approve。詳細指令見 `docs/FAST_E2E_RELEASE_RUNTIME.md`。

## 最新檔案規則

- 追版本時先睇 source file `LastWriteTime`，揀包含最新修改意圖嗰份 source tree。
- 一開始 pass 後，唔再用修改日期決定 release；以 pass receipt 入面 `frontendBundle.sha256` 為唯一版本證據。
- Bake 必須 deterministic mirror 完整受管 frontend，唔准逐個檔靠人手估邊份新。
