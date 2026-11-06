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
echo "== sign-in probe (saved login replayed in a separate browser; no password printed)"
sudo -n docker compose -f deploy/compose.yaml exec -T cowagent timeout 240 python - <<'PY' 2>&1 | grep -v '^\[' | tail -60
import sys, re; sys.path.insert(0, "/app")
from config import load_config; load_config()
from agent import lrm_logins as logins
from playwright.sync_api import sync_playwright
def show(p, label):
    try: body = re.sub(r"\s+", " ", p.inner_text("body", timeout=3000))[:300]
    except Exception as e: body = f"<{type(e).__name__}>"
    fields = p.evaluate("() => [...document.querySelectorAll('input,button')].filter(e => e.offsetParent).map(e => e.tagName + ':' + (e.type||'') + ':' + (e.name||e.id||e.autocomplete||'') + ':' + (e.innerText||'').trim().slice(0,20)).join(' | ')")
    print(f"  [{label}] url={p.url}\n    fields: {fields}\n    text: {body}\n    signed_out={logins.signed_out(p)}")
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    for s in logins.public():
        print("-", s["host"], "login_url:", s.get("login_url") or "-", "url:", s.get("url"))
        ctx = b.new_context(); p = ctx.new_page()
        resp = []
        p.on("response", lambda r: resp.append(f"{r.request.method} {r.url.split('?')[0][-60:]} {r.status}") if r.request.method == "POST" else None)
        try:
            p.goto(s.get("login_url") or s["url"], timeout=45000, wait_until="domcontentloaded")
            try: p.wait_for_load_state("networkidle", timeout=8000)
            except Exception: pass
            show(p, "before")
            ok, msg = logins.sign_in(p, s.get("login_url") or s["url"])
            print("  sign_in ->", ok, msg)
            p.wait_for_timeout(3000)
            show(p, "after+3s")
            print("    POSTs:", "; ".join(resp[-6:]) or "none")
        except Exception as e:
            print("  probe error:", type(e).__name__, str(e)[:160])
        ctx.close()
    b.close()
PY
