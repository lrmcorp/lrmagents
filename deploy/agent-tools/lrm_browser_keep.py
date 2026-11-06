"""Keep the agents' browser signed in to the owner's apps.

Why it signed out: the browser closes itself after a few idle minutes, and many sites
sign in with session cookies (no expiry date) that a browser drops when it closes; many
also end a session after a while without activity.

1. Every cookie, session cookies included, is saved when the browser closes and when a
   check runs, and put back when it starts again (browser_service is patched to call
   save_cookies / restore_cookies). Apps on the list that keep the sign-in in the page's
   own storage (localStorage / sessionStorage, which a new tab or a restart loses) get
   that saved too, only while they show signed in, and put back into every new tab.
2. "Keep signed in" (Browser page): a list of the owner's apps. Every 20 minutes each one
   is opened in a background tab, which keeps its session active, and checked: if it
   shows a sign-in page, the owner is told on WhatsApp once (sign in again on the Browser
   page; the check then sees it and says it is back).

3. When an agent opens a page and lands on a sign-in page, the saved login signs it in
   again and the page it asked for is opened; without one, the app is added to the list
   and the owner is told once (after_navigate, called by browser_service's navigate).

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


def _storage_file():
    return _root() / "lrm_browser_storage.json"


def _kept_hosts():
    return {urlparse(s["url"]).hostname for s in sites() if urlparse(s["url"]).hostname}


def _is_kept(origin, hosts):
    host = urlparse(origin).hostname or ""
    return any(host == h or host.endswith("." + h) or h.endswith("." + host) for h in hosts)


def _sites_file():
    return _root() / "lrm_browser_keep.json"


# Cookies

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
    _save_storage(context)


_READ_STORAGE = """() => ({ origin: location.origin,
  local: Object.fromEntries(Object.keys(localStorage).map((k) => [k, localStorage.getItem(k)])),
  session: Object.fromEntries(Object.keys(sessionStorage).map((k) => [k, sessionStorage.getItem(k)])) })"""


def _save_storage(context):
    """Page storage of the kept apps' open tabs, from tabs that show signed in."""
    hosts = _kept_hosts()
    if not hosts:
        return
    try:
        saved = json.loads(_storage_file().read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    changed = False
    for page in list(context.pages):
        try:
            if not page.url.startswith(("http://", "https://")) or not _is_kept(page.url, hosts) or _signed_out(page):
                continue
            data = page.evaluate(_READ_STORAGE)
        except Exception as e:
            logger.debug(f"[BrowserKeep] page storage not read: {e}")
            continue
        if not (data.get("local") or data.get("session")) or len(json.dumps(data)) > 500_000:
            continue
        saved[data["origin"]] = {"local": data.get("local") or {}, "session": data.get("session") or {}, "saved": time.time()}
        changed = True
    if changed:
        path = _storage_file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


_FILL_STORAGE = """(() => { const all = %s; const d = all[location.origin]; if (!d) return;
  for (const [store, items] of [[window.sessionStorage, d.session], [window.localStorage, d.local]])
    for (const [k, v] of Object.entries(items || {})) { try { if (store.getItem(k) === null) store.setItem(k, v); } catch (e) {} } })();"""


def _restore_storage(context):
    """Every new tab of a kept app starts with its saved page storage (missing keys only)."""
    try:
        saved = json.loads(_storage_file().read_text(encoding="utf-8"))
    except Exception:
        return
    hosts = _kept_hosts()
    saved = {o: v for o, v in saved.items() if _is_kept(o, hosts)}
    if not saved:
        return
    context.add_init_script(script=_FILL_STORAGE % json.dumps(saved))
    logger.info(f"[BrowserKeep] page storage restored for {len(saved)} app(s)")


def restore_cookies(context):
    """Put back the session cookies a restart dropped (persistent ones are in the profile)."""
    if context is None:
        return
    try:
        saved = json.loads(_cookie_file().read_text(encoding="utf-8")).get("cookies") or []
    except Exception:
        saved = []
    now = time.time()
    keep = [c for c in saved if c.get("expires", -1) in (-1, None) or c.get("expires", 0) > now]
    session = [c for c in keep if c.get("expires", -1) in (-1, None)]
    if session:
        try:
            context.add_cookies(session)
            logger.info(f"[BrowserKeep] restored {len(session)} session cookie(s)")
        except Exception as e:
            logger.warning(f"[BrowserKeep] session cookies not restored: {e}")
    try:
        _restore_storage(context)
    except Exception as e:
        logger.warning(f"[BrowserKeep] page storage not restored: {e}")


# The list of apps

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
            save_cookies(ctx)  # before closing, so a signed-in check tab is saved too
            try:
                page.close()
            except Exception:
                pass
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
            from agent import lrm_logins
            no_login = [x for x in lost if not lrm_logins.find(x["url"])]
            notify("\n".join(["*Signed out of your apps*"] + [f"- {x['name']}" + (f": {x['why']}" if x.get("why") else "") for x in lost]
                             + ["", "The agents can't read their data until you sign in again: console → Browser → open the app and sign in "
                                "(tick 'remember me' / 'keep me signed in' if it offers it)."]
                             + ([f"To stop this for good, save the login of {', '.join(x['name'] for x in no_login)} (console → Browser → "
                                 "Keep signed in → Login next to it): the agents then sign in again by themselves."] if no_login else [])))
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


# Sign-in pages met while an agent works

_told = {}  # host -> when the owner was last told


def _add_site(url, title):
    host = urlparse(url).hostname or ""
    with _lock:
        items = sites()
        if any((urlparse(x["url"]).hostname or "") == host for x in items) or len(items) >= 30:
            return False
        items.append({"url": f"{urlparse(url).scheme}://{host}/", "name": host, "status": "signed_out", "checked": time.time(),
                      "alerted": time.time(), "detail": (title or "")[:160], "why": "added when an agent found it signed out"})
        _write(items)
    return True


def after_navigate(page, url, current_url, title):
    """Runs on the browser thread after each navigate. Returns (url, title) for the agent."""
    if not url.startswith(("http://", "https://")) or not _signed_out(page):
        return current_url, title
    host = urlparse(url).hostname or ""
    from agent import lrm_logins
    if lrm_logins.find(url):
        ok, why = lrm_logins.sign_in(page, url)
        if ok:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(800)
            except Exception:
                pass
            save_cookies(page.context)
            logger.info(f"[BrowserKeep] {host}: signed in again with the saved login")
            return page.url, page.title()
        logger.warning(f"[BrowserKeep] {host}: saved login failed: {why}")
    added = _add_site(url, title)
    now = time.time()
    if now - _told.get(host, 0) > ALERT_EVERY:
        _told[host] = now
        text = (f"*Sign-in needed: {host}*\n- An agent tried to open it and got the sign-in page.\n"
                f"- Sign in once on console → Browser{' (it is now on Keep signed in, so it stays signed in)' if added else ''}.\n"
                "- Press *Login* next to it (Browser → Keep signed in) to save the login: the agents then sign in by themselves.")
        threading.Thread(target=lambda: _safe_notify(text), daemon=True).start()
    return current_url, f"{title} [sign-in page: {host} is signed out; the owner has been asked to sign in]"


def _safe_notify(text):
    try:
        _notify(text)
    except Exception as e:
        logger.info(f"[BrowserKeep] owner not told: {e}")
