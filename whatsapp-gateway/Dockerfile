FROM node:22-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY src ./src
COPY public ./public
# Session keys live here; the compose file mounts a volume.
RUN mkdir -p /data /media/whatsapp && chown node:node /data /media/whatsapp
USER node
ENV NODE_ENV=production DATA_DIR=/data PORT=8080
EXPOSE 8080
CMD ["node", "src/server.js"]
