#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=src): verify the scheduler's runtime imports resolve.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T cowagent "$@" </dev/null; }
echo "== import check: agent.lrm_task_output present and working"
dc python -c "import sys; sys.path.insert(0,'/app'); import agent.lrm_task_output as o; print('import OK; marker', o.FINAL_OPEN, o.FINAL_CLOSE); print('classify sample:', o.classify('[KIRIM]Laporan uji[/KIRIM]'))" 2>&1
echo "== file present in image"
dc sh -lc 'ls -l /app/agent/lrm_task_output.py'
echo "== scheduler errors in the last 20 min (should be no ImportError)"
dc sh -lc 'true' ; sudo -n docker compose -f deploy/compose.yaml logs --no-color --since 20m cowagent 2>&1 | grep -E "lrm_task_output|No module|_execute_agent_task|executed successfully|result sent" | tail -20
