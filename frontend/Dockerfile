ARG NODE_IMAGE=node:24.19.0-bookworm-slim
ARG CADDY_IMAGE=caddy:2.11.7-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
# Public URL only. No provider keys or backend environment are build arguments.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build
FROM ${CADDY_IMAGE}
COPY --from=build /build/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
