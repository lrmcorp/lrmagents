#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=src): prints upstream (already LRM-patched) scheduler
# source so delivery/output-selection can be read. No secrets are printed.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T cowagent "$@" </dev/null; }
echo "== integration.py (scheduler delivery + output selection)"
dc sh -lc 'nl -ba /app/agent/tools/scheduler/integration.py 2>/dev/null | sed -n "1,260p"'
echo "== scheduler_service.py _execute_task (status done/failed)"
dc sh -lc 'grep -n "def _execute_task\|status\|full_output\|output_sink\|failed\|done\|preview" /app/agent/tools/scheduler/scheduler_service.py 2>/dev/null | head -60'
