# CARDZ Market Cap：GitHub Push → AWS Pull Deploy

本文件只描述 **AWS/Node production**。Cloudflare 如有使用，只是 DNS／HTTPS／Tunnel
入口，不是應用程式部署目標。真正部署動作必須在 AWS server 發生：

```text
Windows 3308 + WSL operator
  → 產生 baked snapshot / assets
  → commit 到 GitHub main（訊息含 [deploy]）
  → GitHub push webhook
  → AWS webhook receiver 驗證簽名、branch、commit message
  → AWS checkout fast-forward + Git LFS pull
  → docker compose up --build
  → container /api/health
  → https://app.cardzmarketcap.com/api/health
```

AWS production **不連接 Windows MySQL 3308**。容器只讀：

- `data/public/seed-snapshot.json`
- 該 snapshot 引用的 `data/public/market-assets/*.webp`

## 0. 固定值

IT 開工前只需填好以下值；其餘命令不應自行改路徑：

```bash
DEPLOY_USER=<AWS Linux deploy user，例如 cardzdeploy>
REPO_DIR=<AWS checkout 絕對路徑，例如 /opt/cardz-market-cap>
REPO_SSH_URL=git@github.com:jacksoncoin0202-ops/cardz-market-cap.git
PUBLIC_HEALTH_URL=https://app.cardzmarketcap.com/api/health
WEBHOOK_PUBLIC_URL=<AWS webhook 的 HTTPS URL>
```

目前 GitHub repository 的 production branch 是 `main`，Docker 入口是根目錄
`compose.yaml`，容器內 health endpoint 是 `/api/health`。

## 1. 一次性：讓 AWS 只讀拉取 private GitHub repo

在 AWS server 以 `$DEPLOY_USER` 產生專用 key；不要重用個人 SSH key：

```bash
sudo -u "$DEPLOY_USER" ssh-keygen \
  -t ed25519 \
  -f "/home/$DEPLOY_USER/.ssh/cardz_market_cap_deploy" \
  -C "cardz-market-cap-aws-readonly" \
  -N ""

sudo -u "$DEPLOY_USER" cat "/home/$DEPLOY_USER/.ssh/cardz_market_cap_deploy.pub"
```

將輸出的 **public key** 加到 GitHub：

```text
Repository → Settings → Deploy keys → Add deploy key
Title: cardz-market-cap AWS readonly
Allow write access: 關閉
```

AWS 的 `/home/$DEPLOY_USER/.ssh/config`：

```sshconfig
Host github-cardz-market-cap
  HostName github.com
  User git
  IdentityFile ~/.ssh/cardz_market_cap_deploy
  IdentitiesOnly yes
```

把 repo URL 固定為專用 host alias，然後 clone：

```bash
sudo -u "$DEPLOY_USER" git clone \
  git@github-cardz-market-cap:jacksoncoin0202-ops/cardz-market-cap.git \
  "$REPO_DIR"

sudo -u "$DEPLOY_USER" git -C "$REPO_DIR" lfs install --local
sudo -u "$DEPLOY_USER" git -C "$REPO_DIR" switch main
```

AWS 必須預先安裝 Git、Git LFS、Docker Engine 及 Docker Compose v2。`$DEPLOY_USER`
必須有權操作該 repo 和 Docker，但不需要 GitHub write 權限。

## 2. 一次性：AWS deploy command

deploy command 必須由 AWS host 擁有，不由 webhook payload 拼 shell command。將以下內容安裝為：

```text
/usr/local/libexec/cardz-market-cap-deploy
```

檔案 owner 應為 `root:root`、mode `0755`：

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=<AWS checkout 絕對路徑>
PUBLIC_HEALTH_URL=https://app.cardzmarketcap.com/api/health
EXPECTED_SHA="${1:?GitHub after SHA is required}"

[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]

exec 9>/run/lock/cardz-market-cap-deploy.lock
flock -n 9

cd "$REPO_DIR"

test -z "$(git status --porcelain)"
git fetch --prune origin main

TARGET_SHA="$(git rev-parse origin/main)"
test "$TARGET_SHA" = "$EXPECTED_SHA"

CURRENT_SHA="$(git rev-parse HEAD)"
git merge-base --is-ancestor "$CURRENT_SHA" "$TARGET_SHA"

git switch main
git merge --ff-only "$TARGET_SHA"
git lfs pull

export CARDZ_PUBLIC_BUILD_ID="$(git rev-parse --short=8 HEAD)"

docker compose up \
  --build \
  --detach \
  --remove-orphans \
  --wait \
  --wait-timeout 180

INTERNAL_HEALTH="$(curl --fail --silent --show-error \
  http://127.0.0.1:3000/api/health)"
PUBLIC_HEALTH="$(curl --fail --silent --show-error \
  "$PUBLIC_HEALTH_URL")"

python3 - "$CARDZ_PUBLIC_BUILD_ID" "$INTERNAL_HEALTH" "$PUBLIC_HEALTH" <<'PY'
import json
import sys

expected_build = sys.argv[1]
internal = json.loads(sys.argv[2])
public = json.loads(sys.argv[3])

for name, result in (("internal", internal), ("public", public)):
    assert result["status"] == "ok", (name, result)
    assert result["build"] == expected_build, (name, result)
    assert result["cards"] == 762, (name, result)

assert public["generation"] == internal["generation"], (internal, public)
print(json.dumps({"internal": internal, "public": public}, ensure_ascii=False))
PY
```

重要行為：

- `flock -n` 保證同一時間只得一個 deploy owner。
- 只接受 GitHub payload 的完整 40-character SHA，而且必須等於 `origin/main`。
- 只接受 fast-forward；AWS checkout 有手動改動時直接停止。
- `git lfs pull` 必須在 Docker build 前完成，否則圖片只會係 LFS pointer。
- build ID 直接用 production commit SHA 前八位。
- Docker health、內部 health、公開 health 必須同一次 deploy 對得上。

## 3. 一次性：開放 GitHub webhook

AWS webhook receiver 必須提供一個公開 HTTPS endpoint，並在執行 deploy command 前同時驗證：

1. `X-Hub-Signature-256` HMAC-SHA256 正確；
2. `payload.ref == refs/heads/main`；
3. `payload.head_commit.message` 包含 literal `[deploy]`；
4. 傳入 deploy command 的唯一參數是 `payload.after`；
5. 同一個 delivery ID 不可重複執行。

Webhook secret 只存在 AWS secret store／root-readable service environment 和 GitHub webhook
設定，禁止寫入 repo、deploy log 或聊天。

GitHub 設定：

```text
Repository → Settings → Webhooks → Add webhook
Payload URL: <WEBHOOK_PUBLIC_URL>
Content type: application/json
Secret: <與 AWS receiver 相同的 secret>
SSL verification: Enable SSL verification
Events: Just the push event
Active: 開啟
```

Receiver 應把 webhook HTTP request 與實際 deploy job 分開：簽名及 trigger 通過後回 `202`
並記錄 delivery ID、target SHA、開始時間、結束狀態。單純回 HTTP `200/202` 只代表接單，
不代表 Docker build 或公開 health 已成功。

AWS Security Group 不應直接公開 Docker port `3000`。公開流量由現有 ALB／Nginx／Tunnel
入口轉到 AWS origin；webhook endpoint 亦必須經 HTTPS。

## 4. 每次出 Live：Jackson 只做這段

先在 Windows/WSL 完成 refresh、snapshot materialization 和 FE build。production commit 只可包含
公開 snapshot、公開 market assets、前端及 deployment source；禁止加入 `.env`、cookies、tokens、
`data/runtime/private-source-map/` 或其他 private collector material。

部署只在 release commit 成為 `main` HEAD 時觸發：

```bash
git switch main
git pull --ff-only origin main

# 將已完成的 release commit fast-forward 到 main；不要重做 snapshot。
git merge --ff-only <approved-release-branch-or-commit>

git commit --allow-empty \
  -m "release: CARDZ Market Cap <release-id> <fe-id> [deploy]"

git push origin main
```

如果 release commit 本身已經包含 `[deploy]`，就不需要額外 `--allow-empty` commit；直接將該 commit
fast-forward 到 `main` 再 push。

## 5. 一次 E2E 的完成標準

一次 deploy 只有同時具備以下證據先算成功：

```text
GitHub main HEAD SHA
  == webhook payload.after
  == AWS checkout HEAD
  == CARDZ_PUBLIC_BUILD_ID 的完整來源

AWS docker compose service == healthy
internal /api/health.status == ok
public /api/health.status == ok
internal build == public build == main HEAD short SHA
internal cards == public cards == 762
internal generation == public generation
```

GitHub webhook 顯示 `2xx` 但 AWS job 無成功紀錄，或公開 `/api/health` 對唔上 build／generation，
都不算完成。未查清原 job 前不可 redeliver、不可再推第二個 `[deploy]` commit。

## 6. 今次 release 應見到的值

```text
GitHub main commit: 78848067a8ad1f4a5fac5d2ab380ae49512099cf
CARDZ_PUBLIC_BUILD_ID: 78848067
DB projection generation: db3308_ab0b51aa013eb50b
Product snapshot generation: product_subset_20260807T094818Z
Public snapshot SHA-256: 8e800a2ac69a03a4de6e8635075e37e75b3c2f42a6095d890af02471839e7ce8
Expected cards: 762
Presentation: FE02
```

AWS receiver 恢復後，應處理現有 `78848067` delivery，而不是再製造一個 release。公開
`/api/health` 必須回 `build=78848067`、`cards=762`，並與容器內 generation 一致。
