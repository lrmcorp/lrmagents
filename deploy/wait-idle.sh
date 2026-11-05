#!/usr/bin/env bash
# Runs on the VPS just before the containers are restarted: waits (up to 15 minutes) until
# no agent is working, so a redeploy never cuts off a report, a chat answer or a scheduled
# task halfway (agent/lrm_busy.py counts them; /api/lrm/busy reports the count).
cd ~/cowagent || exit 0
token=$(grep -m1 '^COWAGENT_API_TOKEN=' .env.whatsapp 2>/dev/null | cut -d= -f2- | tr -d "'\"")
[[ -n "$token" ]] || exit 0
deadline=$(( $(date +%s) + ${WAIT_IDLE_SECONDS:-900} ))
while :; do
  state=$(curl -fsS --max-time 5 -H "Authorization: Bearer $token" http://127.0.0.1:9899/api/lrm/busy 2>/dev/null) || exit 0  # not running or older image
  runs=$(printf '%s' "$state" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("runs",0)+d.get("scheduled",0))' 2>/dev/null || echo 0)
  [[ "$runs" == 0 ]] && { echo "agents idle: restarting"; exit 0; }
  if (( $(date +%s) >= deadline )); then echo "::warning::agents still busy ($state) after waiting; restarting anyway"; exit 0; fi
  echo "waiting for $runs agent run(s) to finish: $state"
  sleep 15
done
