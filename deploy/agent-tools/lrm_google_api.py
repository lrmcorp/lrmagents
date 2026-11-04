"""/api/lrm/google and /api/lrm/google/callback: the Connections page (console login required).

GET                                   -> connection state, redirect URI, morning-brief settings
POST {op: "client", client_id, client_secret}
POST {op: "connect"}                  -> {url}: Google's sign-in page
POST {op: "disconnect"}
POST {op: "brief", enabled, time, agent}
POST {op: "brief_test"}               -> runs the brief now and sends it to WhatsApp
GET  /api/lrm/google/callback         -> where Google sends the browser back
Installed as channel/web/api/lrm_google.py by the LRMagent image; importing it
starts the morning-brief scheduler.
"""

import html
import json
import re
import threading

import web

from channel.web.core._common import _require_auth
from common.log import logger


def _host():
    return web.ctx.env.get("HTTP_X_FORWARDED_HOST") or web.ctx.env.get("HTTP_HOST", "")


def _ok(**data):
    return json.dumps({"status": "success", **data}, ensure_ascii=False)


def _fail(message):
    return json.dumps({"status": "error", "message": str(message)}, ensure_ascii=False)


class LrmGoogleHandler:
    def GET(self):
        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        from agent import lrm_google as g
        return _ok(**g.public_view(), redirect_uri=g.redirect_uri(_host()))

    def POST(self):
        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        from agent import lrm_google as g
        try:
            body = json.loads(web.data() or b"{}")
        except ValueError:
            return _fail("invalid JSON")
        op = body.get("op")
        try:
            if op == "client":
                cid, secret = str(body.get("client_id", "")).strip(), str(body.get("client_secret", "")).strip()
                if not cid.endswith(".apps.googleusercontent.com") or len(secret) < 10:
                    return _fail("That doesn't look like a Google OAuth client ID and secret (the ID ends in .apps.googleusercontent.com).")
                g.update(client_id=cid, client_secret=secret)
                return _ok(**g.public_view())
            if op == "connect":
                return _ok(url=g.auth_link(_host()))
            if op == "disconnect":
                g.disconnect()
                return _ok(**g.public_view())
            if op == "brief":
                time_text = str(body.get("time") or "07:00")
                if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_text):
                    return _fail("Time must be HH:MM, e.g. 07:00.")
                brief = {**(g.load().get("brief") or {}), "enabled": bool(body.get("enabled")), "time": time_text,
                         "agent": str(body.get("agent") or "")}
                g.update(brief=brief)
                return _ok(**g.public_view())
            if op == "brief_test":
                threading.Thread(target=lambda: _quiet(g.run_brief), daemon=True).start()
                return _ok(message="Preparing the brief now; it arrives on your WhatsApp in a minute or two.")
            return _fail("unknown op")
        except g.GoogleError as e:
            return _fail(e)
        except Exception as e:
            logger.error(f"[Google] {op} failed: {e}")
            return _fail(e)


def _quiet(fn):
    try:
        fn()
    except Exception:
        pass


class LrmGoogleCallbackHandler:
    def GET(self):
        _require_auth()
        web.header("Content-Type", "text/html; charset=utf-8")
        from agent import lrm_google as g
        params = web.input(code="", state="", error="")
        if params.error:
            message, ok = f"Google did not connect: {params.error}.", False
        else:
            try:
                who = g.finish(params.code, params.state, _host())
                message, ok = f"Connected{f' as {who}' if who else ''}. You can close this tab.", True
            except Exception as e:
                message, ok = str(e), False
        return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>Google · LRMagent</title><body style=\"font:15px/1.5 Inter,system-ui,sans-serif;background:#f9f9fb;color:#0a0a0f;"
                f"display:grid;place-items:center;min-height:100vh;margin:0\"><div style=\"background:#fff;border:1px solid #dbdbe5;"
                f"border-radius:14px;padding:28px 32px;max-width:440px;text-align:center\"><div style=font-size:34px>{'✅' if ok else '⚠️'}</div>"
                f"<p>{html.escape(message)}</p><p><a href='/' style=color:#4f5bd5>Back to LRMagent</a></p></div>"
                f"<script>try{{window.opener&&window.opener.postMessage('lrm-google-done','*')}}catch(e){{}}</script>")


try:
    from agent import lrm_google as _g
    _g.start_scheduler()
except Exception as _e:  # never stop the console from starting
    logger.warning(f"[MorningBrief] scheduler not started: {_e}")

try:
    from agent import lrm_memory as _m
    _m.start()
except Exception as _e:  # never stop the console from starting
    logger.warning(f"[ChatMemory] not started: {_e}")
