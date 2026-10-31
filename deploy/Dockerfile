# LRMagent: the upstream agent image plus the LRM brand, skills and tools.
ARG BASE_IMAGE=zhayujie/chatgpt-on-wechat@sha256:5e3d2bad4c4d63cb71cbf1acaa83b7cd10f07832fcf2a96c2a20f132075ae933
FROM ${BASE_IMAGE}

COPY deploy/brand /tmp/brand
# Obsidian skills (kepano/obsidian-skills, MIT), copied into every workspace at startup.
COPY deploy/skills /app/skills/
# switch_model tool: change model by asking in chat (registered by apply.py).
COPY deploy/agent-tools/model_switch /app/agent/tools/model_switch/
# transcribe_audio tool: voice notes from WhatsApp.
COPY deploy/agent-tools/transcribe /app/agent/tools/transcribe/
# Scheduled messages from WhatsApp chats are delivered back through the gateway.
COPY deploy/agent-tools/whatsapp_relay.py /app/channel/lrm_whatsapp_relay.py
# Usage per agent: recorder, API and page (page is copied with the brand files).
COPY deploy/agent-tools/lrm_usage.py /app/agent/lrm_usage.py
COPY deploy/agent-tools/lrm_usage_api.py /app/channel/web/api/lrm_usage.py
# Errors in chat explained in plain words (hooked in by apply.py).
COPY deploy/agent-tools/lrm_errors.py /app/agent/lrm_errors.py
# Vault: Obsidian-style page over each agent's memory and knowledge (page copied with the brand files).
COPY deploy/agent-tools/lrm_vault_api.py /app/channel/web/api/lrm_vault.py
# /media/whatsapp: whichever container mounts the WhatsApp media volume first sets its
# owner, so make it the gateway's user (node, uid 1000) here too.
RUN set -eux; \
    python /tmp/brand/apply.py /app /tmp/brand/theme.json; \
    static=/app/channel/web/static; \
    mkdir -p "$static/brand"; \
    cp /tmp/brand/img/*.png /tmp/brand/fonts/* /tmp/brand/brand.css /tmp/brand/usage.html /tmp/brand/vault.html /tmp/brand/vault.js "$static/brand/"; \
    cp /tmp/brand/img/logo.jpg "$static/logo.jpg"; \
    cp /tmp/brand/img/favicon.ico "$static/favicon.ico"; \
    chmod -R a+rX "$static"; \
    mkdir -p /media/whatsapp && chown 1000:1000 /media/whatsapp; \
    rm -rf /tmp/brand
