"""Keep the agents' browser signed in to the owner's apps.

Why it signed out: the browser closes itself after a few idle minutes, and many sites
sign in with session cookies (no expiry date) that a browser drops when it closes; many
also end a session after a while without activity.

1. Every cookie, session cookies included, is saved when the browser closes and when a
   check runs, and put back when it starts again (browser_service is patched to call
   save_cookies / restore_cookies).
2. "Keep signed in" (Browser page): a list of the owner's apps. Every 20 minutes each one
   is opened in a background tab, which keeps its session active, and checked: if it
   shows a sign-in page, the owner is told on WhatsApp once (sign in again on the Browser
   page; the check then sees it and says it is back).

Installed as agent/lrm_browser_keep.py; /api/lrm/browser serves the list.
"""
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from common.log import logger

CHECK_EVERY = int(os.environ.get("LRMAGENT_KEEP_SIGNED_IN_MINUTES") or 20) * 60
ALERT_EVERY = 12 * 3600
_lock = threading.Lock()


def _root():
    from config import get_data_root
    return Path(get_data_root())


def _cookie_file():
    return _root() / "lrm_browser_cookies.json"


def _sites_file():
    return _root() / "lrm_browser_keep.json"


# ---- cookies ---------------------------------------------------------------------------

def save_cookies(context):
    """All cookies of the browser (session cookies too). Runs on the browser thread."""
    if context is None:
        return
    try:
        cookies = context.cookies()
    except Exception as e:
        logger.debug(f"[BrowserKeep] cookies not read: {e}")
        return
    path = _cookie_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved": time.time(), "cookies": cookies}), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def restore_cookies(context):
    """Put back the session cookies a restart dropped (persistent ones are in the profile)."""
    if context is None:
        return
    try:
        saved = json.loads(_cookie_file().read_text(encoding="utf-8")).get("cookies") or []
    except Exception:
        return
    now = time.time()
    keep = [c for c in saved if c.get("expires", -1) in (-1, None) or c.get("expires", 0) > now]
    session = [c for c in keep if c.get("expires", -1) in (-1, None)]
    if not session:
        return
    try:
        context.add_cookies(session)
        logger.info(f"[BrowserKeep] restored {len(session)} session cookie(s)")
    except Exception as e:
        logger.warning(f"[BrowserKeep] session cookies not restored: {e}")


# ---- the list of apps --------------------------------------------------------------------

def sites():
    try:
        return json.loads(_sites_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(items):
    path = _sites_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def set_sites(rows):
    """Replace the list (keeping each site's last status)."""
    old = {s["url"]: s for s in sites()}
    out = []
    for r in rows or []:
        url = str(r.get("url") or "").strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        host = urlparse(url).hostname
        if not host:
            continue
        name = str(r.get("name") or "").strip()[:60] or host
        prev = old.get(url, {})
        out.append({"url": url, "name": name, "status": prev.get("status", "unchecked"), "checked": prev.get("checked"),
                    "alerted": prev.get("alerted"), "detail": prev.get("detail", "")})
    with _lock:
        _write(out[:30])
    return out[:30]


_LOGIN_URL = re.compile(r"(log-?in|sign-?in|signon|auth(orize)?|sso|account/?s?/?login|session/new|oauth|/u/login)", re.I)


def _signed_out(page):
    """A sign-in page: the address says so, or a visible password field."""
    url = page.url or ""
    try:
        password = page.locator("input[type=password]:visible").count() > 0
    except Exception:
        password = False
    return bool(_LOGIN_URL.search(urlparse(url).path + "?" + (urlparse(url).query or ""))) or password


def _check_one(service, site):
    """Open the site in a background tab (never the agent's own), see whether it is signed in."""
    def run():
        ctx = service._context
        page = ctx.new_page()
        try:
            page.goto(site["url"], timeout=45000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            out = _signed_out(page)
            why = ""
            if out:
                # A saved login (agent/lrm_logins.py): sign in again without the owner.
                try:
                    from agent import lrm_logins
                    if lrm_logins.find(site["url"]):
                        ok, why = lrm_logins.sign_in(page, site["url"])
                        out = not ok
                except Exception as e:
                    why = f"saved login failed: {type(e).__name__}"
            return {"signed_out": out, "url": page.url, "title": (page.title() or "")[:80], "why": why, "auto": bool(why) and not out}
        finally:
            try:
                page.close()
            except Exception:
                pass
            save_cookies(ctx)
    return service._submit(run)


def _browser_busy_with_owner():
    try:
        from agent import lrm_activity as act
        return act.browser_control().get("who") == "owner"
    except Exception:
        return False


def _notify(text):
    from config import conf
    token = str(conf().get("external_api_token") or "")
    url = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
    req = urllib.request.Request(f"{url}/whatsapp/internal/notify-owner", method="POST",
                                 data=json.dumps({"agentId": "default", "text": text}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def check_all(notify=_notify):
    items = sites()
    if not items or _browser_busy_with_owner():
        return items
    from agent.tools.browser.browser_tool import BrowserTool
    service = BrowserTool._shared_service
    if service is None:
        BrowserTool()._get_service()
        service = BrowserTool._shared_service
    now = time.time()
    lost, back = [], []
    for site in items:
        try:
            r = _check_one(service, site)
            was = site.get("status")
            site["status"] = "signed_out" if r["signed_out"] else "signed_in"
            site["why"] = (r.get("why") or "")[:200] if r["signed_out"] else ""
            site["detail"] = (("signed in again with the saved login · " if r.get("auto") else "") + r["title"])[:160]
            if r["signed_out"] and (not site.get("alerted") or now - site["alerted"] > ALERT_EVERY):
                site["alerted"] = now
                lost.append(site)
            if not r["signed_out"]:
                if was == "signed_out":
                    back.append(site)
                site["alerted"] = None
        except Exception as e:
            site["status"] = "error"
            site["detail"] = str(e)[:120]
        site["checked"] = now
    with _lock:
        current = {s["url"]: s for s in sites()}
        for s in items:
            if s["url"] in current:
                current[s["url"]].update({k: s.get(k) for k in ("status", "checked", "alerted", "detail", "why")})
        _write(list(current.values()))
    try:
        if lost:
            notify("\n".join(["*Signed out of your apps*"] + [f"- {s['name']}" + (f": {s['why']}" if s.get("why") else "") for s in lost]
                             + ["", "The agents can't read their data until you sign in again: console → Browser → open the app and sign in "
                                "(tick 'remember me' / 'keep me signed in' if it offers it)."]))
        if back:
            notify("\n".join(["✅ *Signed in again*"] + [f"- {s['name']}" for s in back]))
    except Exception as e:
        logger.info(f"[BrowserKeep] owner not told: {e}")
    return items


_started = False


def start_background():
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(120)
        while True:
            try:
                check_all()
            except Exception as e:
                logger.warning(f"[BrowserKeep] check failed: {e}")
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, name="lrm-browser-keep", daemon=True).start()
