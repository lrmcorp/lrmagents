#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=net): confirms the sandbox has no internet egress
# but cowagent can still reach it. No secrets printed.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T "$@" </dev/null; }
echo "== sandbox networks (should be sandbox_net only)"
sudo -n docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(sudo -n docker compose -f deploy/compose.yaml ps -q sandbox)" 2>/dev/null
echo "== sandbox -> internet (expect failure)"
dc sandbox python3 -c "import socket; socket.setdefaulttimeout(5); 
try:
    socket.create_connection(('api.github.com',443)); print('REACHED internet (BAD)')
except Exception as e: print('blocked (good):', type(e).__name__)"
echo "== cowagent -> sandbox:8099 (expect reachable)"
dc cowagent python3 -c "import socket; socket.setdefaulttimeout(5)
try:
    socket.create_connection(('sandbox',8099)); print('cowagent reaches sandbox:8099 (good)')
except Exception as e: print('cowagent CANNOT reach sandbox (BAD):', type(e).__name__)"
