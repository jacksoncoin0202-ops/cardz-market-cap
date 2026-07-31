# syntax=docker/dockerfile:1
#
# CARDZ Market Cap — 標準 Node runtime image（AWS ECS / EC2 / 任何食 container 嘅地方）。
# 同 Cloudflare Workers 路徑並存：呢個 image 完全唔用 wrangler / OpenNext。
#
# Build context 一定要係 repo root（要 npm workspaces + data/public/），唔係 apps/web：
#   docker build -f apps/web/Dockerfile -t cardz-web:latest .

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

# prebuild 會 tsc build @cardz/market-data，再由 sync-snapshot.mjs 逐張核對
# data/public/market-assets 嘅 sha256 然後抄入 apps/web/public/market-assets。
# 圖對唔上 hash 就會喺呢一步 fail，唔會出到一個爛 image。
WORKDIR /repo/apps/web
RUN npm run build

# apps/web/scripts/prepare-standalone.mjs 已經將 .next/static 同非市場 public
# assets 放入 standalone 樹；市場圖必須經 generation-aware route。Docker 同
# WSL 共用同一個可驗證 build contract。


FROM node:24-bookworm-slim AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    CARDZ_RUNTIME=node \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=builder --chown=node:node /repo/apps/web/.next/standalone ./

USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "apps/web/server.js"]
