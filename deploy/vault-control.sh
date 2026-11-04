#!/usr/bin/env bash
# Bridge between the console's Vault → Git panel and the vault sync on this host.
# Runs every minute from cron. The panel (inside the LRMagent container) writes
# requests into ~/vault-sync/control, which compose mounts into the container:
#   config-request.json  {"repo": "owner/repo"} to set the repo ("" turns sync off)
#   sync-request          run a sync now
# and this script answers in state.json: repo, public deploy key, last status and
# log lines, whether a sync is running. The key's private half never leaves ~/.ssh.
set -uo pipefail
BASE="$HOME/vault-sync"
CONTROL="$BASE/control"
KEY="$HOME/.ssh/lrmagent_vault"
mkdir -p "$CONTROL" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
chmod 777 "$CONTROL"

repo_ok() { [[ "$1" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; }

if [[ -f "$CONTROL/config-request.json" ]]; then
  wanted="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1])).get("repo","")).strip())' "$CONTROL/config-request.json" 2>/dev/null || echo '!invalid')"
  rm -f "$CONTROL/config-request.json"
  current="$(cat "$BASE/repo-name" 2>/dev/null || true)"
  if [[ "$(cat "$BASE/repo-source" 2>/dev/null)" == github ]]; then
    echo "The repo is set by the LRMAGENT_VAULT_REPO GitHub variable; change it there." > "$BASE/control-error"
  elif [[ -z "$wanted" ]]; then
    rm -f "$BASE/repo-name" "$BASE/status" "$BASE/retries" "$BASE/control-error"
    rm -rf "$BASE/repo"
  elif repo_ok "$wanted"; then
    rm -f "$BASE/control-error"
    if [[ "$wanted" != "$current" ]]; then
      printf '%s\n' "$wanted" > "$BASE/repo-name"
      echo console > "$BASE/repo-source"
      # A different repo starts from a fresh clone and fresh sync markers.
      rm -rf "$BASE/repo" "$BASE/status" "$BASE/retries"
      touch "$CONTROL/sync-request"
    fi
  else
    echo "\"$wanted\" is not a GitHub repo name (owner/repo)." > "$BASE/control-error"
  fi
fi

# The deploy key exists from the start, so the panel can show it before the repo does.
[[ -f "$KEY" ]] || ssh-keygen -q -t ed25519 -N '' -C lrmagent-vault@vps -f "$KEY"

syncing=false
if [[ -f "$CONTROL/sync-request" && -s "$BASE/repo-name" ]]; then
  rm -f "$CONTROL/sync-request"
  nohup bash "$HOME/cowagent/deploy/vault-sync.sh" >> "$BASE/sync.log" 2>&1 &
  syncing=true
  sleep 2
fi
rm -f "$CONTROL/sync-request"
if [[ -e "$BASE/lock" ]] && ! flock -n "$BASE/lock" true 2>/dev/null; then syncing=true; fi

python3 - "$BASE" "$KEY.pub" "$syncing" > "$CONTROL/state.json.tmp" <<'PY'
import json, os, sys, time
base, pub, syncing = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
def read(name, default=""):
    try:
        with open(os.path.join(base, name)) as f:
            return f.read().strip()
    except OSError:
        return default
status = read("status")
kind, _, rest = status.partition(" ")
when, _, message = rest.partition(" ")
log = read("sync.log").splitlines()[-15:]
try:
    with open(pub) as f:
        key = f.read().strip()
except OSError:
    key = ""
print(json.dumps({
    "repo": read("repo-name"), "source": read("repo-source") or "", "deploy_key": key,
    "status": kind, "status_time": when, "message": message, "error": read("control-error"),
    "syncing": syncing, "log": log, "updated": int(time.time()),
}))
PY
mv "$CONTROL/state.json.tmp" "$CONTROL/state.json"
chmod 644 "$CONTROL/state.json"
