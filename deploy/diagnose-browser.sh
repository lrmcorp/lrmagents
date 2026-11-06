#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=browser): the agents' browser sign-ins only. The workflow encrypts this output before it reaches the
# public Actions log; nothing here prints secrets or env files.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml "$@" </dev/null; }
echo "== browser sign-ins (Keep signed in list, saved logins, cookies per app)"
sudo -n docker compose -f deploy/compose.yaml exec -T cowagent python - <<'PY' 2>&1 | grep -v '^\[' | tail -60
import sys, json, time; sys.path.insert(0, "/app")
from config import load_config, get_data_root; load_config()
from pathlib import Path
from urllib.parse import urlparse
from agent import lrm_browser_keep as keep, lrm_logins as logins
now = time.time()
saved = {s["host"]: s for s in logins.public()}
print("saved logins:", ", ".join(f"{h} (user {'set' if s.get('username') else 'none'}, saved {s.get('updated')})" for h, s in saved.items()) or "none")
try:
    cookies = json.loads((Path(get_data_root()) / "lrm_browser_cookies.json").read_text()).get("cookies") or []
except Exception as e:
    cookies = []; print("cookie file:", e)
for s in keep.sites():
    host = urlparse(s["url"]).hostname or ""
    base = ".".join(host.split(".")[-2:])
    mine = [c for c in cookies if base and c.get("domain", "").lstrip(".").endswith(base)]
    sess = [c for c in mine if c.get("expires", -1) in (-1, None)]
    exp = sorted(c["expires"] for c in mine if c.get("expires", -1) not in (-1, None))
    login = logins.find(s["url"])
    try:
        store = json.loads((Path(get_data_root()) / "lrm_browser_storage.json").read_text())
    except Exception:
        store = {}
    st = [o for o in store if (urlparse(o).hostname or "").endswith(base)]
    kept = [o + " (" + str(len(store[o].get("session") or {})) + " session keys, " + str(len(store[o].get("local") or {})) + " local keys)" for o in st]
    print("  page storage saved for:", ", ".join(kept) or "none")
    print(f"- {s['name']} ({host}): {s.get('status')}, checked {round((now - (s.get('checked') or now)) / 60)} min ago, "
          f"saved login: {'yes (' + login[0] + ')' if login else 'NO'}; cookies {len(mine)} ({len(sess)} session-only"
          + (f", soonest expiry in {round((exp[0] - now) / 3600, 1)} h" if exp else "") + f"); detail: {s.get('detail', '')[:90]}; why: {s.get('why', '')[:120]}")
PY
dc logs --no-color --since 48h cowagent 2>&1 | grep -E 'BrowserKeep|\[Logins\]|lrm_logins|site_login' | cut -c1-240 | tail -40
echo "== browser and HRD lines, last 24h"
dc logs --no-color --since 24h cowagent 2>&1 | grep -iE '\[Browser\]|browser_tool|HRD|hrd' | grep -viE 'debug' | cut -c1-260 | tail -40
