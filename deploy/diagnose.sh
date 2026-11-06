#!/usr/bin/env bash
# Runs on the VPS (piped over SSH by .github/workflows/diagnose.yml): what is running
# and what went wrong lately. The workflow encrypts this output before it reaches the
# public Actions log; nothing here prints secrets or env files.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml "$@" </dev/null; }
echo "== revision"; cat .deploy-revision 2>/dev/null
echo "== machine"; nproc; free -m; df -h / | tail -1; sudo -n docker stats --no-stream --format "{{.Name}} {{.MemUsage}}" 2>/dev/null
echo "== containers"; dc ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
echo "== gateway health"; dc exec -T whatsapp-gateway node -e "fetch('http://127.0.0.1:8080/whatsapp/health').then(r=>r.text()).then(console.log)" 2>&1
echo "== voice"; dc exec -T cowagent python -c "import urllib.request;print(urllib.request.urlopen('http://voice:8095/health',timeout=10).read().decode())" 2>&1 | tail -1; dc logs --no-color --tail 30 voice 2>&1 | tail -8
echo "== gateway log"; dc logs --no-color --tail 400 whatsapp-gateway 2>&1 | grep -iE 'error|fail|warn|connect|close|logged|agent call|restart|private vault|relay' | tail -80
echo "== agent errors"; dc logs --no-color --tail 4000 cowagent 2>&1 | grep -iE 'error|exception|traceback|fallback|lrmplans|insufficient|402|401|OpenAI API|ChatMemory|failed|Personal|Voice|\[Origin\]' | tail -120
echo "== last agent tracebacks"; dc logs --no-color --tail 4000 cowagent 2>&1 | grep -A25 'Traceback' | tail -120
echo "== teammates log"; dc logs --no-color --tail 200 teammates 2>&1 | tail -40
echo "== agent state"; sudo -n docker compose -f deploy/compose.yaml exec -T cowagent sh -c 'cd /app && PYTHONPATH=/app python - <<PY 2>&1 | grep -v "^\[" | tail -40
from config import load_config, conf
try: load_config()
except Exception as e: print("load_config", e)
c = conf()
print("model:", c.get("model"), "| bot_type:", c.get("bot_type"))
print("chat_fallback:", c.get("chat_fallback"))
for k in ("deepseek_api_key", "claude_api_key", "open_ai_api_key", "gemini_api_key"):
    print(k, "set" if c.get(k) else "empty")
from agent.registry import get_agent_registry
for p in get_agent_registry().list():
    print("agent:", p.id, "| enabled:", p.enabled, "| model:", getattr(p, "model", None), getattr(p, "bot_type", None))
import agent.lrm_plans as lp
print("plans:", lp.models())
import json as _j, urllib.request as _u
for cp in c.get("custom_providers") or []:
    print("custom provider:", cp.get("id"), cp.get("name"), cp.get("api_base"), cp.get("models") or cp.get("model"), "key set" if cp.get("api_key") else "no key")
    if "openrouter.ai" in str(cp.get("api_base")):
        try:
            ids = {m["id"] for m in _j.load(_u.urlopen("https://openrouter.ai/api/v1/models", timeout=20))["data"]}
            for m in cp.get("models") or [cp.get("model")]:
                print("  on OpenRouter:", m, m in ids, sorted(i for i in ids if i.startswith(m.split("-")[0]) and "deepseek" in i)[:8] if m not in ids else "")
        except Exception as e:
            print("  model list failed:", e)
        for m in cp.get("models") or [cp.get("model")]:
            try:
                req = _u.Request(cp["api_base"].rstrip("/") + "/chat/completions", data=_j.dumps({"model": m, "max_tokens": 5, "messages": [{"role": "user", "content": "Reply with OK"}]}).encode(),
                                 headers={"Authorization": "Bearer " + cp["api_key"], "Content-Type": "application/json"})
                r = _j.load(_u.urlopen(req, timeout=60))
                print("  answer:", m, repr(r["choices"][0]["message"]["content"])[:40])
            except Exception as e:
                body = getattr(e, "read", lambda: b"")()[:200]
                print("  answer failed:", m, e, body)
import agent.lrm_personal as pp
pr = pp.profile()
print("personal:", pr and (pr.id, pr.name, pr.workspace))
import os, json
from config import get_data_root
cfg = os.path.join(get_data_root(), "config.json")
for cfg in ("/app/config.json", os.path.join(get_data_root(), "config.json"), "/home/agent/.cow/config.json"):
    if os.path.exists(cfg):
        try:
            print("agents in", cfg, [(a.get("id"), a.get("name")) for a in json.load(open(cfg)).get("agents", [])])
        except Exception as e:
            print(cfg, e)
PY' </dev/null
echo "== live test: one message to the personal assistant"
dc exec -T cowagent sh -c 'cd /app && PYTHONPATH=/app python - <<PY 2>&1 | grep -v "^\[" | tail -15
import json, time, urllib.request, urllib.error
from config import load_config, conf
load_config()
port = int(conf().get("web_port") or 9899)
body = {"agent_id": "personal", "conversation_id": "lrm-voice:personal:diagnose", "stream": False,
        "messages": [{"role": "user", "content": "Diagnostic check from the owner: reply with just OK."}]}
req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                             headers={"Authorization": "Bearer " + str(conf().get("external_api_token") or ""), "Content-Type": "application/json"})
t = time.time()
try:
    r = json.load(urllib.request.urlopen(req, timeout=600))
    print("answer:", repr(r["choices"][0]["message"]["content"])[:200], "model:", r.get("model"), f"{time.time()-t:.0f}s")
except urllib.error.HTTPError as e:
    print("failed:", e.code, e.read()[:1200])
except Exception as e:
    print("failed:", e)
PY' </dev/null
echo "== fallback during the test"; dc logs --no-color --since 15m cowagent 2>&1 | grep -iE 'fallback|LLM API error|failed across|402|usage limit|lrmplans' | cut -c1-380 | tail -25
echo "== voice timing (text-to-speech, then speech-to-text of it)"
sudo -n docker compose -f deploy/compose.yaml exec -T cowagent python - <<'PY' 2>&1 | grep -v '^\[' | tail -8
import json, time, urllib.request
from config import load_config, conf
load_config()
tok = str(conf().get("external_api_token") or "")
def call(path, data, ctype):
    t = time.time()
    req = urllib.request.Request("http://voice:8095" + path, data=data, method="POST", headers={"Authorization": "Bearer " + tok, "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read(), r.headers.get("Content-Type"), time.time() - t
for lang, text in (("en", "Yes, I can hear you loud and clear. What can I do for you?"), ("id", "Ya, saya bisa mendengar Anda dengan jelas.")):
    try:
        wav, ctype, secs = call("/tts", json.dumps({"text": text, "lang": lang, "format": "wav"}).encode(), "application/json")
        print(f"tts {lang}: {len(wav)} bytes {ctype} in {secs:.1f}s")
        out, _, secs = call("/stt", wav, "application/octet-stream")
        print(f"stt {lang}: {json.loads(out).get('text')!r} in {secs:.1f}s")
    except Exception as e:
        print(f"{lang} failed: {e} {getattr(e, 'read', lambda: b'')()[:200]!r}")
PY
echo "== scheduled tasks"
sudo -n docker compose -f deploy/compose.yaml exec -T cowagent python - <<'PY' 2>&1 | grep -v '^\[' | tail -20
import sys; sys.path.insert(0, "/app")
from config import load_config; load_config()
from agent.tools.scheduler.integration import get_task_store
for t in get_task_store().list_tasks() if hasattr(get_task_store(), "list_tasks") else []:
    a = t.get("action") or {}
    print(t.get("id"), t.get("agent_id"), repr(t.get("name"))[:40], t.get("schedule"), a.get("type"), a.get("channel_type"), str(a.get("receiver"))[-30:], "enabled" if t.get("enabled") else "off", "next", t.get("next_run_at"))
PY
echo "== recent tool calls and scheduler"
dc logs --no-color --since 24h cowagent 2>&1 | grep -E '🔧|\[Scheduler\]|scheduler' | cut -c1-260 | tail -40
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
    print(f"- {s['name']} ({host}): {s.get('status')}, checked {round((now - (s.get('checked') or now)) / 60)} min ago, "
          f"saved login: {'yes (' + login[0] + ')' if login else 'NO'}; cookies {len(mine)} ({len(sess)} session-only"
          + (f", soonest expiry in {round((exp[0] - now) / 3600, 1)} h" if exp else "") + f"); detail: {s.get('detail', '')[:90]}; why: {s.get('why', '')[:120]}")
PY
dc logs --no-color --since 48h cowagent 2>&1 | grep -E 'BrowserKeep|\[Logins\]|lrm_logins|site_login' | cut -c1-240 | tail -40
