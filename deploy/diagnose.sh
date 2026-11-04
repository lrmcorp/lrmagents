#!/usr/bin/env bash
# Runs on the VPS (piped over SSH by .github/workflows/diagnose.yml): what is running
# and what went wrong lately. The workflow encrypts this output before it reaches the
# public Actions log; nothing here prints secrets or env files.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml "$@" </dev/null; }
echo "== revision"; cat .deploy-revision 2>/dev/null
echo "== containers"; dc ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
echo "== gateway health"; dc exec -T whatsapp-gateway node -e "fetch('http://127.0.0.1:8080/whatsapp/health').then(r=>r.text()).then(console.log)" 2>&1
echo "== gateway log"; dc logs --no-color --tail 400 whatsapp-gateway 2>&1 | grep -iE 'error|fail|warn|connect|close|logged|agent call|restart' | tail -80
echo "== agent errors"; dc logs --no-color --tail 4000 cowagent 2>&1 | grep -iE 'error|exception|traceback|fallback|lrmplans|insufficient|402|401|OpenAI API|ChatMemory|failed' | tail -120
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
PY' </dev/null
