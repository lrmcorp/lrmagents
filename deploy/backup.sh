#!/usr/bin/env bash
# Nightly LRMagent backup: agent workspaces (memory, knowledge vault, skills),
# settings, and WhatsApp sessions, one tarball per volume per night.
# Installed in cron by the deploy workflow. Restore steps: README "Backups".
set -euo pipefail

DEST="${BACKUP_DIR:-$HOME/backups/lrmagent}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
STAMP="$(date -u +%Y%m%d-%H%M)"
VOLUMES=(cowagent_workspace cowagent_data cowagent_whatsapp)

umask 077
mkdir -p "$DEST"
chmod 700 "$DEST"

for volume in "${VOLUMES[@]}"; do
  if ! sudo -n docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) skip $volume (no such volume)"
    continue
  fi
  out="$DEST/$volume-$STAMP.tar.gz"
  # Read-only mount; the archive streams out so the helper never writes to the host as root.
  sudo -n docker run --rm -v "$volume":/v:ro alpine:3.20 tar -C /v -czf - . > "$out.part"
  mv "$out.part" "$out"
  echo "$(date -u +%FT%TZ) saved $out ($(du -h "$out" | cut -f1))"
done

find "$DEST" -name '*.tar.gz' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name '*.part' -mmin +120 -delete
