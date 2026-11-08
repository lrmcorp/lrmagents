#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=src): prints the scheduler save path and the console's
# scheduler fetch URLs so the receivers[] save can be traced. No secrets are printed.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T cowagent "$@" </dev/null; }
echo "== api/scheduler.py: route dispatch + create/update + receivers"
dc sh -lc 'grep -n "def \|op ==\|action_type\|/scheduler\|receivers\|path\|self.path\|action_in\|updates\[" /app/api/scheduler.py 2>/dev/null | head -60'
echo "== console JS files that mention scheduler"
dc sh -lc 'grep -rln "scheduler" /app/channel/web/static 2>/dev/null | head'
echo "== how the console calls scheduler create/update (fetch URLs)"
dc sh -lc 'grep -rnoE "/api/scheduler[a-zA-Z0-9/_?=-]*|scheduler\?op=[a-z]+|op=.(create|update)" /app/channel/web/static 2>/dev/null | sort -u | head -40'
