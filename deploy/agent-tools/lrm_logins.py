"""Saved logins: usernames and passwords for the owner's working sites (Daily Dose, an HR
portal), so the agents' browser can sign in again by itself.

- Set on the console's Browser page (Keep signed in). Passwords are write-only there:
  the console never shows one again.
- Stored encrypted (AES-GCM) in the data folder with a key that lives outside it
  (LRMAGENT_LOGINS_KEY, made once on the server), so backups and the vault's GitHub copy
  never hold a readable password.
- Agents sign in with the site_login tool: they name the site, the browser types the login;
  the password is never given to the model or written in a reply.
- Keep signed in uses it too: a site found signed out is signed in again on its own, and
  the owner is told only when that fails (a one-time code, a captcha).

The agents run in the same container as the browser that has to type the password, so this
is not the Private vault: keep banking and anything critical there.
Installed as agent/lrm_logins.py.
"""
import base64
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from common.log import logger

_lock = threading.Lock()


def _file():
    from config import get_data_root
    return Path(get_data_root()) / "lrm_logins.json"


def _key():
    raw = os.environ.get("LRMAGENT_LOGINS_KEY", "").strip()
    if len(raw) < 64:
        raise RuntimeError("saved logins are not set up on this server (LRMAGENT_LOGINS_KEY missing)")
    return bytes.fromhex(raw[:64])


def _seal(text):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + AESGCM(_key()).encrypt(nonce, text.encode(), b"lrm-login")).decode()


def _open(blob):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(blob)
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], b"lrm-login").decode()


def host_of(url):
    url = str(url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _load():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data):
    path = _file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def save(url, username, password, login_url=""):
    host = host_of(url)
    if not host:
        raise ValueError("give the site's address")
    username = str(username or "").strip()
    if not username:
        raise ValueError("give the username or email")
    with _lock:
        data = _load()
        old = data.get(host, {})
        if not password and not old.get("secret"):
            raise ValueError("give the password")
        data[host] = {"url": url if "://" in str(url) else "https://" + str(url), "login_url": str(login_url or old.get("login_url") or ""),
                      "username": username, "secret": _seal(password) if password else old["secret"], "updated": time.time()}
        _write(data)
    return public()


def delete(url):
    host = host_of(url)
    with _lock:
        data = _load()
        data.pop(host, None)
        _write(data)
    return public()


def public():
    """What the console and the agents may see: never the password."""
    return [{"host": h, "url": v.get("url"), "username": v.get("username"), "updated": v.get("updated")} for h, v in sorted(_load().items())]


def find(url):
    """The saved login for a page's site (example.com also covers app.example.com)."""
    host = host_of(url)
    data = _load()
    best = None
    for saved, entry in data.items():
        if host == saved or host.endswith("." + saved):
            if best is None or len(saved) > len(best[0]):
                best = (saved, entry)
    return best


# Signing in

_USER_SEL = ("input[autocomplete=username]:visible, input[type=email]:visible, "
             "input[name*=user i]:visible, input[id*=user i]:visible, input[name*=email i]:visible, input[id*=email i]:visible, "
             "input[name*=login i]:visible, input[id*=login i]:visible, input[name*=phone i]:visible")
_PASS_SEL = "input[type=password]:visible"
_SIGNIN_TEXT = re.compile(r"^\s*(sign ?in|log ?in|masuk|login|continue|next|lanjut|berikutnya)\s*$", re.I)
_LOGIN_URL = re.compile(r"(log-?in|sign-?in|signon|auth|sso|session/new|/u/login)", re.I)


def _click_text(page, pattern):
    for role in ("button", "link"):
        try:
            loc = page.get_by_role(role, name=pattern)
            if loc.count():
                loc.first.click(timeout=4000)
                return True
        except Exception:
            continue
    return False


def signed_out(page):
    try:
        if page.locator(_PASS_SEL).count() > 0:
            return True
        # the first step of a two-step sign-in: a username or email field and a Sign in / Next button
        if page.locator(_USER_SEL).count() > 0 and page.get_by_role("button", name=_SIGNIN_TEXT).count() > 0:
            return True
    except Exception:
        pass
    parsed = urlparse(page.url or "")
    return bool(_LOGIN_URL.search(parsed.path + "?" + (parsed.query or "")))


def sign_in(page, url=None):
    """Sign in on this page (or the saved site) with the saved login. Runs on the browser
    thread. Returns (ok, message); the password never leaves this function."""
    target = url or page.url
    found = find(target)
    if not found:
        return False, f"No saved login for {host_of(target) or 'this site'}. The owner adds it on the Browser page (Keep signed in)."
    host, entry = found
    password = _open(entry["secret"])
    try:
        start = entry.get("login_url") or url or entry["url"]
        if url or entry.get("login_url") or host_of(page.url) != host:
            page.goto(start, timeout=45000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        if not signed_out(page):
            return True, f"Already signed in to {host}."
        if page.locator(_PASS_SEL).count() == 0 and page.locator(_USER_SEL).count() == 0:
            _click_text(page, re.compile(r"(sign ?in|log ?in|masuk)", re.I))
            page.wait_for_timeout(2500)
        user = page.locator(_USER_SEL)
        if user.count() == 0:
            # the text field right before the password field
            user = page.locator("input:not([type=hidden]):not([type=password]):not([type=checkbox]):not([type=submit]):visible")
        def still_on_site():
            # A redirect or a sign-in step may move to another domain: never type there.
            h = host_of(page.url)
            return h == host or h.endswith("." + host)
        if not still_on_site():
            return False, f"The sign-in page moved to {host_of(page.url)}, not {host}: the saved login was not used."
        if user.count():
            user.first.fill(entry["username"], timeout=8000)
        if page.locator(_PASS_SEL).count() == 0:  # two steps: the username, then the password
            if not _click_text(page, _SIGNIN_TEXT):
                page.keyboard.press("Enter")
            page.wait_for_selector(_PASS_SEL, timeout=15000)
        if not still_on_site():
            return False, f"The sign-in page moved to {host_of(page.url)}, not {host}: the password was not typed."
        page.locator(_PASS_SEL).first.fill(password, timeout=8000)
        try:  # "remember me" when the site offers it
            page.get_by_label(re.compile(r"(remember|keep me|ingat)", re.I)).first.check(timeout=1500)
        except Exception:
            pass
        page.locator(_PASS_SEL).first.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            page.wait_for_timeout(4000)
        if signed_out(page):
            text = ""
            try:
                text = page.inner_text("body", timeout=3000)[:2000].lower()
            except Exception:
                pass
            if re.search(r"(otp|verification code|kode verifikasi|one-time|2-step|two-factor|authenticator)", text):
                return False, f"{host} asks for a one-time code: the owner has to finish this sign-in on the Browser page."
            if re.search(r"(captcha|not a robot|bukan robot)", text):
                return False, f"{host} shows a captcha: the owner has to finish this sign-in on the Browser page."
            return False, f"Signing in to {host} did not work (wrong or changed password?). The owner can check it on the Browser page."
        return True, f"Signed in to {host}."
    except Exception as e:
        logger.warning(f"[Logins] sign-in to {host} failed: {type(e).__name__}")
        return False, f"Signing in to {host} failed: {type(e).__name__}. The owner can sign in on the Browser page."
    finally:
        password = None  # noqa: F841
