# syntax=docker/dockerfile:1
#
# CARDZ Market Cap — AWS / Node runtime image。
#
# Build context 一定要係 repo root（要 npm workspaces + data/public/），唔係 apps/web：
#   docker build -f apps/web/Dockerfile -t cardz-web:latest .
#
# 卡圖（data/public/market-assets，1.4GB）唔喺 image 入面，要靠 compose 掛 volume。
# 淨用 docker build / docker run 而唔經 compose 嘅話，記住自己加：
#   -v "$PWD/data/public/market-assets:/app/data/public/market-assets:ro"

FROM node:24-bookworm-slim AS deps
WORKDIR /repo
COPY package.json package-lock.json .npmrc ./
COPY apps/web/package.json apps/web/
COPY packages/market-data/package.json packages/market-data/
RUN npm ci


FROM node:24-bookworm-slim AS builder
WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY . .

ARG CARDZ_PUBLIC_BUILD_ID=docker
ENV CARDZ_PUBLIC_BUILD_ID=${CARDZ_PUBLIC_BUILD_ID} \
    CARDZ_BUILD_TARGET=node \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /repo
RUN npm run build

FROM node:24-bookworm-slim AS runner
WORKDIR /app

ARG CARDZ_PUBLIC_BUILD_ID=deploy
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    CARDZ_PUBLIC_BUILD_ID=${CARDZ_PUBLIC_BUILD_ID} \
    MARKET_DATA_SNAPSHOT_PATH=/app/data/public/seed-snapshot.json \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=builder --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=node:node /repo/data/public/seed-snapshot.json ./data/public/seed-snapshot.json

# data/public/market-assets 唔會烘焙入 image —— 由 compose 掛 :ro volume 入
# /app/data/public/market-assets。詳細原因見 .dockerignore。
# ⚠ 冇掛 volume 嘅話卡圖會 404（loadNodeMarketAsset readFile 失敗回 null），
#   但 /api/health 照樣 200，因為 snapshot 係烘焙入 image 嘅。

USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "apps/web/server.js"]
