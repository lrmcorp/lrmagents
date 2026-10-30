"""GET /api/lrm/usage?days=30 -> usage rows for the LRMagent Usage page (console login required).
Installed as channel/web/api/lrm_usage.py by the LRMagent image."""

import json

import web

from channel.web.core._common import _require_auth


class LrmUsageHandler:
    def GET(self):
        _require_auth()
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.lrm_usage import summary
        try:
            days = int(web.input(days="30").days)
        except ValueError:
            days = 30
        return json.dumps({"status": "success", **summary(min(max(days, 1), 366))})
