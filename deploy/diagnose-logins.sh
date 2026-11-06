#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=logins): replays each saved login in a separate browser. No password is printed.
cd ~/cowagent || exit 1
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
