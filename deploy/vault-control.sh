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
# The container can plant symlinks, FIFOs or directories in $CONTROL, so this script
# never writes through a path there: temp files live in $BASE (host only) and are
# published with a rename, which replaces a symlink instead of following it.
mkdir -p "$CONTROL" "$HOME/.ssh"
chmod 700 "$HOME/.ssh" "$BASE"
# Writable for the container's agent user (gid 999, set by the deploy) and nobody else;
# the sticky bit stops the container from deleting or replacing state.json.
[[ "$(stat -c %g "$CONTROL")" == 999 ]] || sudo -n chgrp 999 "$CONTROL" 2>/dev/null
# Without the group (no sudo) the panel still needs write access; the sticky bit stays.
if [[ "$(stat -c %g "$CONTROL")" == 999 ]]; then chmod 1770 "$CONTROL"; else chmod 1777 "$CONTROL"; fi

repo_ok() { [[ "$1" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; }

# Prints the requested repo, or !invalid. Opens without following symlinks, and only a
# small regular file, so a planted link to another file or a FIFO is never read.
read_request() {
  python3 - "$1" <<'PY' 2>/dev/null || echo '!invalid'
import json, os, stat, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
st = os.fstat(fd)
if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
    raise SystemExit(1)
data = json.loads(os.read(fd, 4096).decode("utf-8"))
print(str(data.get("repo", "")).strip())
PY
}

want_sync=false
if [[ -e "$CONTROL/config-request.json" || -L "$CONTROL/config-request.json" ]]; then
  wanted="$(read_request "$CONTROL/config-request.json")"
  rm -rf "$CONTROL/config-request.json"
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
      want_sync=true
    fi
  else
    # The rejected text is never echoed back: it came from the container.
    echo "Invalid repository name: use owner/repo." > "$BASE/control-error"
  fi
fi

# The deploy key exists from the start, so the panel can show it before the repo does.
[[ -f "$KEY" ]] || ssh-keygen -q -t ed25519 -N '' -C lrmagent-vault@vps -f "$KEY"

syncing=false
if [[ -e "$CONTROL/sync-request" || -L "$CONTROL/sync-request" ]]; then want_sync=true; fi
rm -rf "$CONTROL/sync-request"
if [[ "$want_sync" == true && -s "$BASE/repo-name" ]]; then
  nohup bash "$HOME/cowagent/deploy/vault-sync.sh" >> "$BASE/sync.log" 2>&1 &
  syncing=true
  sleep 2
fi
if [[ -e "$BASE/lock" ]] && ! flock -n "$BASE/lock" true 2>/dev/null; then syncing=true; fi

tmp="$(mktemp "$BASE/state.json.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
python3 - "$BASE" "$KEY.pub" "$syncing" > "$tmp" <<'PY'
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
chmod 644 "$tmp"
# A directory planted at state.json would make the rename fail; clear it first.
if [[ -d "$CONTROL/state.json" && ! -L "$CONTROL/state.json" ]]; then rm -rf "$CONTROL/state.json"; fi
mv -fT "$tmp" "$CONTROL/state.json"
rm -f "$CONTROL/state.json.tmp"
