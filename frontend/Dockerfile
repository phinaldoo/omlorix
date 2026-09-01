FROM python:3.14.5-alpine@sha256:5a824eb82cc75361f98611f3cfc5091ea33f10a6ccea4d4ebdabbc523b9a1614 AS build

WORKDIR /workspace

COPY frontend ./frontend
COPY script/cache_buster.py ./script/cache_buster.py

RUN python script/cache_buster.py --frontend-dir frontend --output-dir frontend_dist

FROM nginxinc/nginx-unprivileged:1.29.8-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6 AS prod

COPY --from=build /workspace/frontend_dist /usr/share/nginx/html
COPY nginx/default.http.conf.template/default.conf /tmp/omlorix-default.conf
COPY nginx/bin/render-forwarded-for.sh /usr/local/bin/render-forwarded-for.sh
COPY frontend/docker-entrypoint.d/20-render-omlorix-nginx.sh /docker-entrypoint.d/20-render-omlorix-nginx.sh

USER root
RUN sed 's/listen 80;/listen 8080;/' /tmp/omlorix-default.conf > /etc/nginx/omlorix-default.conf.template \
    && rm /tmp/omlorix-default.conf \
    && chmod 755 /usr/local/bin/render-forwarded-for.sh /docker-entrypoint.d/20-render-omlorix-nginx.sh \
    && chown -R 101:101 /usr/share/nginx/html /etc/nginx/conf.d /etc/nginx/omlorix-default.conf.template
USER 101
