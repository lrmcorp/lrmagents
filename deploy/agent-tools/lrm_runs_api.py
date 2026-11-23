"""/api/lrm/runs: the Tasks page's Stop button for a scheduled run (agent/lrm_task_runs.py).
Signed-in console only. Installed as channel/web/api/lrm_runs.py."""
import json

import web

from channel.web.core._common import _require_auth


class LrmRunsHandler:
    def POST(self):
        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(web.data() or b"{}")
        except ValueError:
            body = {}
        if not isinstance(body, dict) or body.get("op") != "stop":
            return json.dumps({"status": "error", "message": "Unknown request."})
        from agent import lrm_task_runs
        result = lrm_task_runs.stop(body.get("run_id"))
        if not result:
            return json.dumps({"status": "error", "message": "This run is not running any more."})
        return json.dumps({"status": "success", "result": result})
