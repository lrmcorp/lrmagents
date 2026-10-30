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

# Off-site copy (optional): when the deploy has set a passphrase and a private
# repo, that night's archives are encrypted (AES-256, PBKDF2) and pushed to the
# repo's "backups" branch, replacing the previous night so the repo stays small.
PASS_FILE="$HOME/.lrmagent-backup-pass"
REPO_FILE="$HOME/.lrmagent-offsite-repo"
KEY="$HOME/.ssh/lrmagent_vault"
if [[ -s "$PASS_FILE" && -s "$REPO_FILE" && -f "$KEY" ]]; then
  repo="$(cat "$REPO_FILE")"
  work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
  bundle="$work/lrmagent-backup-$STAMP.tar.gz.enc"
  ( cd "$DEST" && tar -cf - $(ls -t ./*-"$STAMP".tar.gz) ) \
    | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$PASS_FILE" -out "$bundle"
  size=$(stat -c %s "$bundle")
  if (( size > 95 * 1024 * 1024 )); then
    echo "$(date -u +%FT%TZ) off-site copy skipped: $(du -h "$bundle" | cut -f1) is over GitHub's file limit"
    echo "FAIL $(date -u +%FT%TZ) off-site backup too large for GitHub" > "$DEST/offsite-status"
  else
    (
      cd "$work" && git init -q -b backups . && git add "$(basename "$bundle")" \
        && git -c user.name="LRMagent backup" -c user.email=backup@lrmagent.invalid commit -qm "Encrypted backup $STAMP" \
        && GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$HOME/vault-sync/known_hosts" \
           git push -qf "${OFFSITE_REMOTE_URL:-git@github.com:$repo.git}" backups:backups
    ) && { echo "$(date -u +%FT%TZ) off-site copy pushed to $repo (branch backups)"; echo "OK $(date -u +%FT%TZ)" > "$DEST/offsite-status"; } \
      || { echo "$(date -u +%FT%TZ) off-site copy failed"; echo "FAIL $(date -u +%FT%TZ) could not push the encrypted backup to $repo" > "$DEST/offsite-status"; }
  fi
fi
