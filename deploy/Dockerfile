# CowAgent with the LRM brand and look. Only the web console changes.
ARG BASE_IMAGE=zhayujie/chatgpt-on-wechat:latest
FROM ${BASE_IMAGE}

COPY deploy/brand /tmp/brand
RUN set -eux; \
    python /tmp/brand/apply.py /app /tmp/brand/theme.json; \
    static=/app/channel/web/static; \
    mkdir -p "$static/brand"; \
    cp /tmp/brand/img/*.png /tmp/brand/fonts/* /tmp/brand/brand.css "$static/brand/"; \
    cp /tmp/brand/img/logo.jpg "$static/logo.jpg"; \
    cp /tmp/brand/img/favicon.ico "$static/favicon.ico"; \
    chmod -R a+rX "$static"; \
    rm -rf /tmp/brand
