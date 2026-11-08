#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=src): dumps the model/fallback logic in agent_bridge.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T cowagent "$@" </dev/null; }
echo "== agent_bridge.py: model selection + fallback_config + chain + per-agent model"
dc sh -lc 'grep -n "def .*fallback\|fallback_config\|chat_fallback\|def _model\|per.agent\|agent.*model\|use_fallback\|def model\|model_for\|custom:\|chain" /app/bridge/agent_bridge.py 2>/dev/null | head -50'
echo "== fallback_config body"
dc sh -lc 'n=$(grep -n "def _fallback_config\|def fallback_config" /app/bridge/agent_bridge.py | head -1 | cut -d: -f1); [ -n "$n" ] && nl -ba /app/bridge/agent_bridge.py | sed -n "$((n)),$((n+60))p"'
echo "== where per-agent model is applied (registry/profile model)"
dc sh -lc 'grep -rn "profile.model\|agent.*\.model\|override.*model\|cp.get(.model\|builtin_model\|model_override" /app/bridge/agent_bridge.py /app/agent/registry.py 2>/dev/null | head -30'
