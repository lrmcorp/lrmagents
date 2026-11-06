#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=tasks): scheduled tasks and their delivery only.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml "$@" </dev/null; }
echo "== now"; date; TZ=Asia/Jakarta date
echo "== containers"; dc ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
echo "== scheduled tasks"
sudo -n docker compose -f deploy/compose.yaml exec -T cowagent python - <<'PY' 2>&1 | grep -v '^\[' | tail -40
import sys; sys.path.insert(0, "/app")
from config import load_config; load_config()
import json, glob
for f in glob.glob("/home/agent/cow/scheduler/tasks.json"):
    data = json.load(open(f))
    tasks = data.get("tasks", data) if isinstance(data, dict) else data
    tasks = tasks.values() if isinstance(tasks, dict) else tasks
    for t in sorted(tasks, key=lambda t: str(t.get("next_run_at"))):
        print(str(t.get("id"))[:8], "on " if t.get("enabled") else "off", "next", t.get("next_run_at"), "| last", t.get("last_run_at"), "|", str(t.get("name"))[:60])
PY
echo "== scheduler, relay and errors, last 6h"
dc logs --no-color --since 6h cowagent 2>&1 | grep -E '\[Scheduler\]|WhatsAppRelay|FanOut|Traceback|ERROR|busy|wait-idle' | cut -c1-260 | tail -60
echo "== gateway sends, last 6h"
dc logs --no-color --since 6h whatsapp-gateway 2>&1 | grep -iE 'send|deliver|refus|error|fail' | cut -c1-220 | tail -25
