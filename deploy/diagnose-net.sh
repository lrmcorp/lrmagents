#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=net): confirms the sandbox has no internet egress
# but cowagent can still reach it. No secrets printed.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T "$@" </dev/null; }
echo "== sandbox networks (should be sandbox_net only)"
sudo -n docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' cowagent-sandbox-1 2>/dev/null || sudo -n docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(sudo -n docker compose -f deploy/compose.yaml ps -q sandbox)" 2>/dev/null
echo "== sandbox -> internet (expect FAIL/timeout)"
dc sandbox sh -lc 'curl -sS -m 6 -o /dev/null -w "curl exit %{http_code}\n" https://api.github.com 2>&1 || echo "blocked (good): exit $?"'
echo "== sandbox -> DNS (expect failure)"
dc sandbox sh -lc 'getent hosts api.github.com && echo "resolved (unexpected)" || echo "no DNS/route (good)"'
echo "== cowagent -> sandbox:8099 (expect reachable)"
dc cowagent sh -lc 'curl -sS -m 6 -o /dev/null -w "sandbox http %{http_code}\n" http://sandbox:8099/ 2>&1 || echo "cowagent cannot reach sandbox (BAD): exit $?"'
