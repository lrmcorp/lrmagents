"""/api/lrm/vault-updates: the owner's side of vault updates (Vault page → Updates).

GET  -> {pending: [...waiting for the owner], reviewing: [...with Lorium], recent: [...]}
POST {id, decision: approve|reject|undo, content?} -> {item}
POST {all: true, decision: approve|reject}        -> {items}
Signed-in console only. Installed as channel/web/api/lrm_vault_updates.py.
"""
import json

import web

from channel.web.core._common import _require_auth


def _out(status, body):
    web.ctx.status = status
    web.header("Content-Type", "application/json; charset=utf-8")
    return json.dumps(body, ensure_ascii=False)


class LrmVaultUpdatesHandler:
    def GET(self):
        _require_auth()
        from agent import lrm_vault_flow as flow
        return _out("200 OK", {"pending": flow.pending(("owner",)), "reviewing": flow.pending(("review",)), "recent": flow.recent(20),
                               "reviewer": flow.agent_name(flow.reviewer_id())})

    def POST(self):
        _require_auth()
        from agent import lrm_origin
        if not lrm_origin.owner_browser(web.ctx.env):
            return _out("403 Forbidden", {"error": lrm_origin.REFUSED})
        from agent import lrm_vault_flow as flow
        try:
            body = json.loads(web.data() or b"{}")
            if body.get("all"):
                return _out("200 OK", {"items": flow.decide_all(body.get("decision") or "")})
            if body.get("decision") == "undo":
                return _out("200 OK", {"item": flow.revert(str(body.get("id") or ""))})
            item = flow.owner_decide(str(body.get("id") or ""), body.get("decision") or "", body.get("content"))
            return _out("200 OK", {"item": item})
        except ValueError as e:
            return _out("400 Bad Request", {"error": str(e)})


# Reminders, expiry and the daily digest run in the console process.
try:
    from agent import lrm_vault_flow as _flow
    _flow.start_background()
except Exception as _e:  # never stop the console from starting
    from common.log import logger as _log
    _log.warning(f"[VaultFlow] background not started: {_e}")
