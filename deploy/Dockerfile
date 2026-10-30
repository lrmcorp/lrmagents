# CowAgent with the Daily Dose look. Only the web console's static files change.
ARG BASE_IMAGE=zhayujie/chatgpt-on-wechat:latest
FROM ${BASE_IMAGE}

COPY deploy/dailydose-theme /tmp/dailydose-theme
RUN set -eux; \
    web=/app/channel/web; \
    python /tmp/dailydose-theme/apply.py "$web" /tmp/dailydose-theme/theme.json; \
    mkdir -p "$web/static/vendor/fonts/poppins"; \
    cp /tmp/dailydose-theme/fonts/* "$web/static/vendor/fonts/poppins/"; \
    cp /tmp/dailydose-theme/dailydose.css "$web/static/css/dailydose.css"; \
    chmod -R a+rX "$web/static"; \
    rm -rf /tmp/dailydose-theme
