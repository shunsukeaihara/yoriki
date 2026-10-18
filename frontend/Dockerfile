# syntax=docker/dockerfile:1
# yoriki-frontend：nuxt build したNodeサーバー（docs/deployment.md §2）。
# ベースイメージは mise.toml の版（Node 26）に合わせ、digestで固定する
FROM node:26-slim@sha256:0d1e33586362bb9e608c4e751e7a9e7edb55c9ac11461ed5666af696cc461dd4 AS builder
# Node 25以降はcorepackが同梱されないので、pnpmは mise.toml と同じ版をnpmで入れる
RUN npm install -g pnpm@12.13.0
WORKDIR /build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm fetch
COPY . .
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile --offline \
    && pnpm build

FROM node:26-slim@sha256:0d1e33586362bb9e608c4e751e7a9e7edb55c9ac11461ed5666af696cc461dd4
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --from=builder /build/.output ./.output
USER node
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
