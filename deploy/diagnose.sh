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
echo "== gateway log"; dc logs --no-color --tail 400 whatsapp-gateway 2>&1 | grep -iE 'error|fail|warn|connect|close|logged|agent call|restart' | tail -80
echo "== agent errors"; dc logs --no-color --tail 4000 cowagent 2>&1 | grep -iE 'error|exception|traceback|fallback|lrmplans|insufficient|402|401|OpenAI API|ChatMemory|failed|Personal|Voice' | tail -120
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
