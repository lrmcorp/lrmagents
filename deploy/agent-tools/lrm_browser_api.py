"""/api/lrm/browser: watch the agents' browser and take the wheel (console login required).

GET  ?op=state                       -> open?, url, title, viewport, who has control
GET  ?op=shot                        -> the current page as JPEG (204 when no browser is open)
POST {op: "open", url}               -> open the browser on a page (e.g. to sign in to a site)
POST {op: "control", take: true}     -> the owner drives; agent browser calls wait until handed back
GET  ?op=keep                        -> {sites}: the apps kept signed in (agent/lrm_browser_keep.py)
POST {op: "keep", sites: [{name, url}]} / {op: "keep-check"}
POST {op: "input", kind, ...}        -> click {x, y} | type {text} | key {key} | scroll {dy} | navigate {url} | back | forward | reload
Everything runs on the browser's own worker thread, so it never races an agent action.
Installed as channel/web/api/lrm_browser.py by the LRMagent image.
"""

import json
import time

import web

from channel.web.core._common import _require_auth

KEYS = {"Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End",
        "PageUp", "PageDown", "Space"}


def _service(start=False):
    from agent.tools.browser.browser_tool import BrowserTool
    service = BrowserTool._shared_service
    if service is None and start:
        BrowserTool()._get_service()
        service = BrowserTool._shared_service
    return service


def _open(service):
    try:
        return bool(service and service.is_running() and getattr(service, "_page", None) is not None)
    except Exception:
        return False


def _on_page(service, fn):
    """Run fn(page) on the browser thread and return its result."""
    return service._submit(lambda: fn(service._page))


class LrmBrowserHandler:
    def GET(self):
        _require_auth()
        from agent import lrm_activity as act
        op = web.input(op="state").op
        if op == "keep":
            from agent import lrm_browser_keep as keep
            web.header("Content-Type", "application/json; charset=utf-8")
            return json.dumps({"status": "success", "sites": keep.sites(), "every_minutes": keep.CHECK_EVERY // 60}, ensure_ascii=False)
        service = _service()
        if op == "shot":
            data = None
            if _open(service):
                try:
                    data = _on_page(service, lambda p: p.screenshot(type="jpeg", quality=62, timeout=8000))
                except Exception:
                    data = None
            if not data:
                web.ctx.status = "204 No Content"
                return b""
            web.header("Content-Type", "image/jpeg")
            web.header("Cache-Control", "no-store")
            return data
        web.header("Content-Type", "application/json; charset=utf-8")
        state = {"status": "success", "open": _open(service), "control": act.browser_control()}
        if state["open"]:
            try:
                info = _on_page(service, lambda p: {"url": p.url, "title": p.title(), "viewport": p.viewport_size or
                                                    p.evaluate("() => ({width: innerWidth, height: innerHeight})")})
                state.update(info)
            except Exception as e:
                state["error"] = str(e)[:200]
        return json.dumps(state, ensure_ascii=False)

    def POST(self):
        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        from agent import lrm_activity as act
        try:
            body = json.loads(web.data() or b"{}")
        except ValueError:
            return json.dumps({"status": "error", "message": "invalid JSON"})
        op = body.get("op")
        try:
            if op == "keep":
                from agent import lrm_browser_keep as keep
                return json.dumps({"status": "success", "sites": keep.set_sites(body.get("sites") or [])}, ensure_ascii=False)
            if op == "keep-check":
                import threading
                from agent import lrm_browser_keep as keep
                threading.Thread(target=keep.check_all, daemon=True).start()
                return json.dumps({"status": "success"})
            if op == "control":
                act.set_browser_control(bool(body.get("take")))
                return json.dumps({"status": "success", "control": act.browser_control()})
            if op == "open":
                url = str(body.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                service = _service(start=True)
                service.navigate(url)
                act.set_browser_control(True)
                return json.dumps({"status": "success", "control": act.browser_control()})
            if op == "input":
                service = _service()
                if not _open(service):
                    return json.dumps({"status": "error", "message": "No browser is open. Open a page first."})
                if act.browser_control().get("who") != "owner":
                    act.set_browser_control(True)
                act.touch_browser_control()
                kind = body.get("kind")
                if kind == "click":
                    x, y = float(body.get("x", 0)), float(body.get("y", 0))
                    _on_page(service, lambda p: p.mouse.click(x, y))
                elif kind == "type":
                    text = str(body.get("text", ""))[:2000]
                    _on_page(service, lambda p: p.keyboard.type(text))
                elif kind == "key":
                    key = str(body.get("key", ""))
                    if key not in KEYS:
                        return json.dumps({"status": "error", "message": "unsupported key"})
                    _on_page(service, lambda p: p.keyboard.press(" " if key == "Space" else key))
                elif kind == "scroll":
                    dy = max(-3000, min(3000, int(body.get("dy", 400))))
                    _on_page(service, lambda p: p.mouse.wheel(0, dy))
                elif kind == "navigate":
                    url = str(body.get("url") or "").strip()
                    if not url.startswith(("http://", "https://")):
                        url = "https://" + url
                    _on_page(service, lambda p: p.goto(url, timeout=30000))
                elif kind in ("back", "forward", "reload"):
                    _on_page(service, lambda p: getattr(p, {"back": "go_back", "forward": "go_forward", "reload": "reload"}[kind])(timeout=15000))
                else:
                    return json.dumps({"status": "error", "message": "unknown input"})
                time.sleep(0.15)
                return json.dumps({"status": "success"})
            return json.dumps({"status": "error", "message": "unknown op"})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)[:300]})


# Keep the owner's apps signed in: checks every 20 minutes (agent/lrm_browser_keep.py).
try:
    from agent import lrm_browser_keep as _keep
    _keep.start_background()
except Exception as _e:  # never stop the console from starting
    from common.log import logger as _log
    _log.warning(f"[BrowserKeep] not started: {_e}")
