#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=src): prints upstream (already LRM-patched) scheduler
# source so delivery/output-selection can be read. No secrets are printed.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T cowagent "$@" </dev/null; }
echo "== integration.py: delivery/execute (find the function that runs a task and sends)"
dc sh -lc 'grep -n "def .*execute\|agent_reply\|channel.send\|output_sink\|def _deliver\|def _run\|reply.content\|content =\|status" /app/agent/tools/scheduler/integration.py 2>/dev/null'
echo "== integration.py lines 300-560"
dc sh -lc 'nl -ba /app/agent/tools/scheduler/integration.py 2>/dev/null | sed -n "300,560p"'
echo "== scheduler_service.py _execute_task body 350-500"
dc sh -lc 'nl -ba /app/agent/tools/scheduler/scheduler_service.py 2>/dev/null | sed -n "350,500p"'
