# LRMagent teammates: Codex, Claude Code, Gemini CLI and OpenCode (Grok, DeepSeek),
# signed in with the owner's own plans, plus the page and API that drive them.
FROM node:22-bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git curl openssh-client python3 make g++ \
       ripgrep procps less nano jq unzip \
    && rm -rf /var/lib/apt/lists/*
# Pinned to tested versions; the weekly update check reports newer ones.
ARG CODEX_VERSION=0.166.0
ARG CLAUDE_CODE_VERSION=2.1.317
ARG GEMINI_CLI_VERSION=0.66.0
ARG OPENCODE_VERSION=1.19.5
RUN npm install -g --no-audit --no-fund "@openai/codex@${CODEX_VERSION}" "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
       "@google/gemini-cli@${GEMINI_CLI_VERSION}" "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY src ./src
COPY public ./public
# The server runs as root so the teammates (uid 1000, clean environment) cannot read
# its environment; their sign-ins live in /home/node and their projects in /work.
RUN mkdir -p /data /work && chown node:node /work && chmod 700 /data
ENV NODE_ENV=production DATA_DIR=/data PORT=8090 TEAMMATE_HOME=/home/node TEAMMATE_WORK=/work
EXPOSE 8090
CMD ["node", "src/server.js"]
