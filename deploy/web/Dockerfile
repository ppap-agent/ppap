FROM node:22-alpine AS web-build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.30.3 --activate
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web ./
RUN pnpm build

FROM nginx:1.27-alpine

COPY --from=web-build /app/dist /usr/share/nginx/html
COPY apps/web/addon.html apps/web/addon.js apps/web/block-docs-addon-api.js apps/web/app.json apps/web/shell.css /usr/share/nginx/html/
COPY deploy/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/web/entrypoint.sh /entrypoint.sh

ARG PPAP_BUILD_SHA=
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}

RUN printf '%s\n' "{\"git_sha\":\"${PPAP_BUILD_SHA}\"}" > /usr/share/nginx/html/build-identity.json \
    && rm -f /usr/share/nginx/html/runtime-config.json \
    && chmod +x /entrypoint.sh

EXPOSE 4173
ENTRYPOINT ["/entrypoint.sh"]
