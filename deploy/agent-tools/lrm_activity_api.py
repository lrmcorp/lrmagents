"""/api/lrm/activity: the Activity page's API (console login required).

GET  ?days=7&agent=&tool=&status=&q=  -> activity rows, pending and recent approvals, the mode
POST {op: "decide", code, decision}   -> approve / deny a pending action
POST {op: "mode", mode}               -> risky | always | off
The WhatsApp gateway also POSTs decisions here ("YES CODE" in the owner's own chat),
with the internal API token from inside the Docker network.
Installed as channel/web/api/lrm_activity.py by the LRMagent image.
"""

import hmac
import json

import web

from channel.web.core._common import _require_auth


def _internal_caller():
    from config import conf
    token = str(conf().get("external_api_token") or "")
    given = web.ctx.env.get("HTTP_AUTHORIZATION", "").replace("Bearer ", "", 1).strip()
    return bool(token) and not web.ctx.env.get("HTTP_X_FORWARDED_FOR") and hmac.compare_digest(given, token)


class LrmActivityHandler:
    def GET(self):
        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        from agent import lrm_activity as act
        p = web.input(days="7", agent="", tool="", status="", q="")
        try:
            days = int(p.days)
        except ValueError:
            days = 7
        rows, decided = act.query(days, p.agent, p.tool, p.status, p.q.strip())
        return json.dumps({"status": "success", "mode": act.get_mode(), "pending": act.pending(),
                           "approvals": decided, "rows": rows, "timeout": act.TIMEOUT}, ensure_ascii=False)

    def POST(self):
        if not _internal_caller():
            _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        from agent import lrm_activity as act
        try:
            body = json.loads(web.data() or b"{}")
        except ValueError:
            return json.dumps({"status": "error", "message": "invalid JSON"})
        if body.get("op") == "decide" and body.get("all"):
            # "yes all" / "no all": every vault update waiting for the owner.
            from agent import lrm_vault_flow as flow
            items = flow.decide_all(body.get("decision"))
            if not items:
                return json.dumps({"status": "error", "message": "No vault updates are waiting."})
            saved = [i for i in items if i["status"] == "applied"]
            names = ", ".join(i["path"].replace("knowledge/", "", 1) for i in items[:6]) + (" …" if len(items) > 6 else "")
            return json.dumps({"status": "success", "message": (f"✅ Saved {len(saved)} to the vault: {names}" if saved
                                                                 else f"🚫 Rejected {len(items)}: {names}")}, ensure_ascii=False)
        if body.get("op") == "decide":
            via = "whatsapp" if _internal_caller() else "console"
            code = str(body.get("code") or "").strip().upper()
            if not code:
                # A plain "yes"/"no": the newest request waiting for the owner, tool approval or vault update.
                from agent import lrm_vault_flow as flow
                waiting = [(r.get("ts") or 0, r["code"]) for r in act.pending()]
                waiting += [(i.get("updated") or 0, i.get("code")) for i in flow._load() if i["status"] == "owner" and i.get("code")]
                code = max(waiting)[1] if waiting else ""
                body["code"] = code
            done = act.decide(body.get("code"), body.get("decision"), via) if code else None
            if not done:
                # A vault update waiting for the owner answers to the same YES/NO CODE replies.
                from agent import lrm_vault_flow as flow
                try:
                    item = flow.decide_by_code(body.get("code"), body.get("decision"))
                except ValueError:
                    item = None
                if item:
                    saved = item["status"] == "applied"
                    return json.dumps({"status": "success", "vault": item["id"], "message":
                                       f"✅ Saved to the vault: {item['path'].replace('knowledge/', '', 1)}" if saved
                                       else f"🚫 Not saved: {item['title']}"}, ensure_ascii=False)
                return json.dumps({"status": "error", "message": "No pending approval with that code (it may have expired)."})
            what = done.get("summary") or done.get("tool_name") or ""
            return json.dumps({"status": "success", "approval": done, "message":
                               (f"✅ Allowed: {what}" if done.get("status") == "approved" else f"🚫 Refused: {what}")}, ensure_ascii=False)
        if body.get("op") == "mode" and not _internal_caller():
            try:
                act.set_mode(body.get("mode"))
            except ValueError as e:
                return json.dumps({"status": "error", "message": str(e)})
            return json.dumps({"status": "success", "mode": act.get_mode()})
        return json.dumps({"status": "error", "message": "unknown op"})
