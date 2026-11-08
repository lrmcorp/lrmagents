#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=src): prints upstream (already LRM-patched) scheduler
# source so delivery/output-selection can be read. No secrets are printed.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T cowagent "$@" </dev/null; }
echo "== integration.py 740-1060 (run_scheduled_task, agent-task + send-message delivery)"
dc sh -lc 'nl -ba /app/agent/tools/scheduler/integration.py 2>/dev/null | sed -n "740,1060p"'
