#!/usr/bin/env bash
# Run on the VPS by the "LRMagent health" workflow. Prints one problem per line
# (nothing when healthy) and always exits 0; the workflow decides what to do.
set -uo pipefail
cd "$HOME/cowagent"
compose() { sudo -n docker compose -f deploy/compose.yaml "$@"; }

if ! curl -fsS --max-time 10 -o /dev/null http://127.0.0.1:9899/; then
  echo "LRMagent console is not answering on the server."
fi
for service in cowagent whatsapp-gateway; do
  state="$(compose ps --format '{{.State}}' "$service" 2>/dev/null | head -1)"
  [[ "$state" == "running" ]] || echo "Container $service is ${state:-missing}."
done
health="$(compose exec -T whatsapp-gateway node -e "fetch('http://127.0.0.1:8080/whatsapp/health').then(r=>r.text()).then(t=>process.stdout.write(t)).catch(()=>process.exit(1))" 2>/dev/null)" \
  || { echo "WhatsApp gateway health endpoint is not answering."; health=''; }
if [[ -n "$health" ]]; then
  # Linked numbers that dropped: logged out on the phone, or gave up reconnecting.
  printf '%s' "$health" | python3 -c '
import json, sys
for a in json.load(sys.stdin).get("accounts", []):
    if a["status"] in ("logged_out", "error"):
        what = "was logged out on the phone" if a["status"] == "logged_out" else "could not reconnect"
        print("WhatsApp for agent {} {}; open the WhatsApp page and link it again.".format(a["agent"], what))
'
fi
used="$(df -P "$HOME" | awk 'NR==2 {gsub("%", "", $5); print $5}')"
[[ "${used:-0}" -ge 90 ]] && echo "VPS disk is ${used}% full."
last="$(ls -t "$HOME"/backups/lrmagent/*.tar.gz 2>/dev/null | head -1)"
if [[ -z "$last" ]] || [[ -n "$(find "$last" -mmin +1560 2>/dev/null)" ]]; then
  echo "No LRMagent backup in the last 26 hours."
fi
status="$(cat "$HOME/vault-sync/status" 2>/dev/null || true)"
case "$status" in
  FAIL*|CONFLICT*) echo "Vault sync: ${status#* * }" ;;
esac
exit 0
