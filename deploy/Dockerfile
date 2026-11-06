# LRMagent: the upstream agent image plus the LRM brand, skills and tools.
ARG BASE_IMAGE=zhayujie/chatgpt-on-wechat@sha256:5e3d2bad4c4d63cb71cbf1acaa83b7cd10f07832fcf2a96c2a20f132075ae933
FROM ${BASE_IMAGE}

COPY deploy/brand /tmp/brand
# Obsidian skills (kepano/obsidian-skills, MIT), copied into every workspace at startup.
COPY deploy/skills /app/skills/
# switch_model tool: change model by asking in chat (registered by apply.py).
COPY deploy/agent-tools/model_switch /app/agent/tools/model_switch/
# ask_teammate tool: hand tasks to Codex, Claude Code, Gemini, Grok or DeepSeek (teammates container).
COPY deploy/agent-tools/teammates /app/agent/tools/teammates/
# transcribe_audio tool: voice notes from WhatsApp.
COPY deploy/agent-tools/transcribe /app/agent/tools/transcribe/
# Scheduled messages from WhatsApp chats are delivered back through the gateway.
COPY deploy/agent-tools/whatsapp_relay.py /app/channel/lrm_whatsapp_relay.py
# Usage per agent: recorder, API and page (page is copied with the brand files).
COPY deploy/agent-tools/lrm_usage.py /app/agent/lrm_usage.py
COPY deploy/agent-tools/lrm_usage_api.py /app/channel/web/api/lrm_usage.py
# Errors in chat explained in plain words (hooked in by apply.py).
COPY deploy/agent-tools/lrm_errors.py /app/agent/lrm_errors.py
COPY deploy/agent-tools/lrm_browser_keep.py /app/agent/lrm_browser_keep.py
COPY deploy/agent-tools/lrm_browser_tabs.py /app/agent/lrm_browser_tabs.py
COPY deploy/agent-tools/lrm_busy.py /app/agent/lrm_busy.py
COPY deploy/agent-tools/lrm_logins.py /app/agent/lrm_logins.py
COPY deploy/agent-tools/lrm_team.py /app/agent/lrm_team.py
# Team page: groups and levels (who reports to whom).
COPY deploy/agent-tools/lrm_org.py /app/agent/lrm_org.py
COPY deploy/agent-tools/lrm_org_api.py /app/channel/web/api/lrm_org.py
COPY deploy/agent-tools/lrm_busy_api.py /app/channel/web/api/lrm_busy.py
# Approvals and activity log: recorder/gate, API and page (page copied with the brand files).
COPY deploy/agent-tools/lrm_activity.py /app/agent/lrm_activity.py
COPY deploy/agent-tools/lrm_origin.py /app/agent/lrm_origin.py
COPY deploy/agent-tools/lrm_activity_api.py /app/channel/web/api/lrm_activity.py
# Gmail, Google Calendar and the morning brief: core, owner check, tool, API (page copied with the brand files).
COPY deploy/agent-tools/lrm_google.py /app/agent/lrm_google.py
COPY deploy/agent-tools/lrm_owner.py /app/agent/lrm_owner.py
COPY deploy/agent-tools/lrm_plans.py /app/agent/lrm_plans.py
COPY deploy/agent-tools/lrm_skills.py /app/agent/lrm_skills.py
COPY deploy/agent-tools/lrm_memory.py /app/agent/lrm_memory.py
COPY deploy/agent-tools/google_tool /app/agent/tools/google_tool/
COPY deploy/agent-tools/whatsapp_tool /app/agent/tools/whatsapp_tool/
# Personal assistant: agent, voice page API, reports from other agents.
COPY deploy/agent-tools/lrm_personal.py /app/agent/lrm_personal.py
COPY deploy/agent-tools/lrm_voice_api.py /app/channel/web/api/lrm_voice.py
COPY deploy/agent-tools/lrm_chats_api.py /app/channel/web/api/lrm_chats.py
# Vault updates: agents propose, Lorium checks, the owner approves.
COPY deploy/agent-tools/lrm_vault_flow.py /app/agent/lrm_vault_flow.py
COPY deploy/agent-tools/lrm_vault_updates_api.py /app/channel/web/api/lrm_vault_updates.py
COPY deploy/agent-tools/vault_update /app/agent/tools/vault_update/
COPY deploy/agent-tools/ask_agent /app/agent/tools/ask_agent/
COPY deploy/agent-tools/team_memory /app/agent/tools/team_memory/
COPY deploy/agent-tools/site_login /app/agent/tools/site_login/
COPY deploy/agent-tools/chat_monitor /app/agent/tools/chat_monitor/
COPY deploy/agent-tools/lrm_google_api.py /app/channel/web/api/lrm_google.py
# Watchable browser: live view and take-over API (page copied with the brand files).
COPY deploy/agent-tools/lrm_browser_api.py /app/channel/web/api/lrm_browser.py
# Sandbox for WhatsApp/API chats' commands (client, and the server the sandbox service runs).
COPY deploy/agent-tools/lrm_sandbox.py /app/agent/lrm_sandbox.py
COPY deploy/agent-tools/lrm_sandbox_server.py /app/agent/lrm_sandbox_server.py
# Voice replies on WhatsApp: text-to-speech as an Opus voice note.
COPY deploy/agent-tools/lrm_tts_api.py /app/channel/web/api/lrm_tts.py
# Vault: Obsidian-style page over each agent's memory and knowledge (page copied with the brand files).
COPY deploy/agent-tools/lrm_vault_api.py /app/channel/web/api/lrm_vault.py
# /media/whatsapp: whichever container mounts the WhatsApp media volume first sets its
# owner, so make it the gateway's user (node, uid 1000) here too.
RUN set -eux; \
    python /tmp/brand/apply.py /app /tmp/brand/theme.json; \
    python -m compileall -q /app/agent /app/channel /app/bridge /app/models > /tmp/compile.log || { cat /tmp/compile.log; exit 1; }; \
    static=/app/channel/web/static; \
    mkdir -p "$static/brand"; \
    cp /tmp/brand/img/*.png /tmp/brand/fonts/* /tmp/brand/brand.css /tmp/brand/usage.html /tmp/brand/vault.html /tmp/brand/vault.js /tmp/brand/topology.js /tmp/brand/personal.html /tmp/brand/shell.js /tmp/brand/activity.html /tmp/brand/connections.html /tmp/brand/browser.html /tmp/brand/team.html "$static/brand/"; \
    cp -r /tmp/brand/vendor "$static/brand/vendor"; \
    python /tmp/brand/fetch_mediapipe.py "$static/brand/vendor/mediapipe"; \
    cp /tmp/brand/img/logo.jpg "$static/logo.jpg"; \
    cp /tmp/brand/img/favicon.ico "$static/favicon.ico"; \
    chmod -R a+rX "$static"; \
    mkdir -p /media/whatsapp && chown 1000:1000 /media/whatsapp; \
    rm -rf /tmp/brand
