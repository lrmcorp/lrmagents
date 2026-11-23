#!/usr/bin/env bash
# Run on the VPS by the "LRMagent health" workflow. Prints one problem per line
# (nothing when healthy) and always exits 0; the workflow decides what to do.
# Each line starts with "<check>: "; the repo is public, so only the check names leave the VPS.
set -uo pipefail
cd "$HOME/cowagent"
compose() { sudo -n docker compose -f deploy/compose.yaml "$@"; }

if ! curl -fsS --max-time 10 -o /dev/null http://127.0.0.1:9899/; then
  echo "console: LRMagent console is not answering on the server."
fi
for service in cowagent whatsapp-gateway teammates sandbox voice; do
  state="$(compose ps --format '{{.State}}' "$service" 2>/dev/null | head -1)"
  [[ "$state" == "running" ]] || echo "container-$service: Container $service is ${state:-missing}."
done
health="$(compose exec -T whatsapp-gateway node -e "fetch('http://127.0.0.1:8080/whatsapp/health').then(r=>r.text()).then(t=>process.stdout.write(t)).catch(()=>process.exit(1))" 2>/dev/null)" \
  || { echo "whatsapp-gateway: WhatsApp gateway health endpoint is not answering."; health=''; }
if [[ -n "$health" ]]; then
  # Linked numbers that dropped: logged out on the phone, or gave up reconnecting.
  printf '%s' "$health" | python3 -c '
import json, sys
for a in json.load(sys.stdin).get("accounts", []):
    if a["status"] in ("logged_out", "error"):
        what = "was logged out on the phone" if a["status"] == "logged_out" else "could not reconnect"
        print("whatsapp-link: WhatsApp for agent {} {}; open the WhatsApp page and link it again.".format(a["agent"], what))
'
fi
# Model calls that failed for good (bad key, no credit, provider down). Counted
# from LRMagent's own log markers; only the count goes into the alert.
failed="$(compose logs --since 15m --no-log-prefix cowagent 2>/dev/null | grep -cE 'LLM API error after|LLM call error \(non-retryable\)' || true)"
[[ "${failed:-0}" -ge 3 ]] && echo "model-calls: ${failed} AI model calls failed in the last 15 minutes: check the API key, credit or the provider's status (Config -> Models)."
used="$(df -P "$HOME" | awk 'NR==2 {gsub("%", "", $5); print $5}')"
[[ "${used:-0}" -ge 90 ]] && echo "disk: VPS disk is ${used}% full."
last="$(ls -t "$HOME"/backups/lrmagent/*.tar.gz 2>/dev/null | head -1)"
if [[ -z "$last" ]] || [[ -n "$(find "$last" -mmin +1560 2>/dev/null)" ]]; then
  echo "backup: No LRMagent backup in the last 26 hours."
fi
# backup.sh writes FAIL here when any volume could not be saved.
backup="$(cat "$HOME/backups/lrmagent/status" 2>/dev/null || true)"
[[ "$backup" == FAIL* ]] && echo "backup: Last backup failed for: ${backup#* * }"
offsite="$(cat "$HOME/backups/lrmagent/offsite-status" 2>/dev/null || true)"
[[ -s "$HOME/.lrmagent-backup-pass" && "$offsite" == FAIL* ]] && echo "offsite-backup: Off-site backup: ${offsite#* * }"
status="$(cat "$HOME/vault-sync/status" 2>/dev/null || true)"
case "$status" in
  FAIL*|CONFLICT*) echo "vault-sync: Vault sync: ${status#* * }" ;;
  RETRY*) [[ "$(cat "$HOME/vault-sync/retries" 2>/dev/null || echo 0)" -ge 3 ]] && echo "vault-sync: Vault sync keeps retrying: ${status#* * }" ;;
esac
# A status older than 3 hours means the hourly sync stopped running.
[[ -n "$status" ]] && [[ -n "$(find "$HOME/vault-sync/status" -mmin +180 2>/dev/null)" ]] && echo "vault-sync: Vault sync has not run for over 3 hours."
exit 0
