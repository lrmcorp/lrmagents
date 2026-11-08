#!/usr/bin/env bash
# Runs on the VPS (diagnose.yml with part=src): dumps the refactored tasks modal so the brand
# "Also send to" hook can be matched to it. No secrets are printed.
cd ~/cowagent || exit 1
dc() { sudo -n docker compose -f deploy/compose.yaml exec -T cowagent "$@" </dev/null; }
echo "== tasks-modal.js: ids, globals, recipient + instance + body building"
dc sh -lc 'grep -nE "id=.task-edit|getElementById\(|task-edit-recipient|instance|recipient|window\.[a-zA-Z]+ *=|currentEditingTask|taskModalMode|selectedTask|body.action|action.receiver|buildBody|create\x27|update\x27|export" /app/channel/web/static/js/views/tasks-modal.js 2>/dev/null | head -80'
echo "== recipient-wrap / markup ids referenced"
dc sh -lc 'grep -noE "task-edit-[a-z-]+|lrm-[a-z-]+" /app/channel/web/static/js/views/tasks-modal.js 2>/dev/null | sort -u'
