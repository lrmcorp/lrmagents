"""/api/lrm/org: the Team page (groups, levels, who reports to whom).

GET  -> {status, lead, groups: [{id, name}], agents: [{id, name, description, lead, private, level, group, reports_to}]}
POST {groups: [{id?, name}], agents: {id: {group, level, reports_to}}} -> the same, saved
Changes are owner-only (agent/lrm_origin.py). Saving refreshes every agent's teammates block.
Installed as channel/web/api/lrm_org.py.
"""
import json
import threading

import web

from channel.web.core._common import _require_auth


def _out(body, status="200 OK"):
    web.ctx.status = status
    web.header("Content-Type", "application/json; charset=utf-8")
    return json.dumps(body, ensure_ascii=False)


class LrmOrgHandler:
    def GET(self):
        _require_auth()
        from agent import lrm_org
        return _out({"status": "success", **lrm_org.view()})

    def POST(self):
        _require_auth()
        from agent import lrm_org, lrm_origin
        if not lrm_origin.owner_browser(web.ctx.env):
            return _out({"status": "error", "message": lrm_origin.REFUSED}, "403 Forbidden")
        try:
            body = json.loads(web.data() or b"{}")
            saved = lrm_org.save(body.get("groups"), body.get("agents"))
        except ValueError as e:
            return _out({"status": "error", "message": str(e)}, "400 Bad Request")

        def refresh():
            from agent import lrm_team
            lrm_team.refresh()
        threading.Thread(target=refresh, name="lrm-team-refresh", daemon=True).start()
        return _out({"status": "success", **saved})
