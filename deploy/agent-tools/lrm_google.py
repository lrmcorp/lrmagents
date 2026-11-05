"""Gmail and Google Calendar for the owner, and the daily morning brief.

The owner creates a Google OAuth client once (Connections page) and connects
their account; tokens stay in a file next to config.json on this server. Agents
reach Gmail and Calendar through the `google` tool, which only the owner may use
(console, the owner's own WhatsApp chat, and the morning brief). Sending mail
and inviting people go through approvals (agent/lrm_activity.py).

The morning brief runs once a day at the chosen time: it asks the chosen agent
for a short brief (today's events, important unread mail, today's reminders) and
sends it to the owner's WhatsApp.
Installed as agent/lrm_google.py by the LRMagent image.
"""

import base64
import datetime
import email.utils
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from common.log import logger

SCOPES = ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/calendar.events"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"
GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
TZ = os.environ.get("TZ", "Asia/Jakarta")

_lock = threading.RLock()
_states = {}


class GoogleError(RuntimeError):
    pass


# Stored settings and tokens

def _path():
    from config import get_data_root
    return os.path.join(get_data_root(), "lrm_google.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data):
    with _lock:
        path = _path()
        tmp = f"{path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)


def update(**changes):
    with _lock:
        data = load()
        data.update(changes)
        save(data)
        return data


def public_view():
    data = load()
    brief = data.get("brief") or {}
    return {
        "client_set": bool(data.get("client_id") and data.get("client_secret")),
        "client_id": data.get("client_id", ""),
        "connected": bool((data.get("tokens") or {}).get("refresh_token")),
        "email": data.get("email", ""),
        "connected_at": data.get("connected_at"),
        "brief": {"enabled": bool(brief.get("enabled")), "time": brief.get("time", "07:00"),
                  "agent": brief.get("agent", ""), "last_run": brief.get("last_run", ""), "last_error": brief.get("last_error", "")},
    }


# OAuth

def redirect_uri(host_header=""):
    base = os.environ.get("WEB_PUBLIC_URL", "").strip().rstrip("/")
    if not base and host_header:
        base = f"https://{host_header}"
    return f"{base}/api/lrm/google/callback"


def auth_link(host_header=""):
    data = load()
    if not (data.get("client_id") and data.get("client_secret")):
        raise GoogleError("Save your Google OAuth client ID and secret first.")
    state = secrets.token_urlsafe(24)
    _states[state] = time.time()
    for s, t in list(_states.items()):
        if time.time() - t > 900:
            _states.pop(s, None)
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": data["client_id"], "redirect_uri": redirect_uri(host_header), "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    })


def _post_form(url, fields):
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        raise GoogleError(body.get("error_description") or body.get("error") or f"Google answered {e.code}")


def finish(code, state, host_header=""):
    if not state or state not in _states:
        raise GoogleError("This sign-in link expired. Start again from the Connections page.")
    _states.pop(state, None)
    data = load()
    tokens = _post_form(TOKEN_URL, {"code": code, "client_id": data["client_id"], "client_secret": data["client_secret"],
                                    "redirect_uri": redirect_uri(host_header), "grant_type": "authorization_code"})
    if not tokens.get("refresh_token"):
        raise GoogleError("Google did not return a long-lived sign-in. Remove LRMagent's access at myaccount.google.com → Security → "
                          "Third-party access, then connect again.")
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600)) - 60
    email_addr = ""
    try:
        payload = tokens.get("id_token", "").split(".")[1]
        email_addr = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")
    except Exception:
        pass
    update(tokens={k: tokens.get(k) for k in ("access_token", "refresh_token", "expires_at", "scope")}, email=email_addr,
           connected_at=time.time())
    return email_addr


def disconnect():
    data = load()
    token = (data.get("tokens") or {}).get("refresh_token")
    if token:
        try:
            _post_form("https://oauth2.googleapis.com/revoke", {"token": token})
        except Exception:
            pass
    data.pop("tokens", None)
    data.pop("email", None)
    save(data)


def _access_token():
    with _lock:
        data = load()
        tokens = data.get("tokens") or {}
        if not tokens.get("refresh_token"):
            raise GoogleError("Google is not connected. Connect it on the console's Connections page.")
        if tokens.get("access_token") and tokens.get("expires_at", 0) > time.time():
            return tokens["access_token"]
        try:
            fresh = _post_form(TOKEN_URL, {"client_id": data["client_id"], "client_secret": data["client_secret"],
                                           "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token"})
        except GoogleError as e:
            if "invalid_grant" in str(e) or "expired" in str(e).lower() or "revoked" in str(e).lower():
                raise GoogleError("The Google sign-in expired or was removed. Connect Google again on the Connections page "
                                  "(if it keeps expiring weekly, publish the OAuth app: Google Cloud → OAuth consent screen → Publish).")
            raise
        tokens.update(access_token=fresh["access_token"], expires_at=time.time() + int(fresh.get("expires_in", 3600)) - 60)
        data["tokens"] = tokens
        save(data)
        return tokens["access_token"]


def _api(method, url, body=None, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read() or b"{}").get("error", {}).get("message")
        except ValueError:
            message = None
        if e.code == 403 and message and "has not been used" in message:
            message = "That Google API is not enabled. In Google Cloud → APIs & Services, enable the Gmail API and Google Calendar API."
        raise GoogleError(message or f"Google answered {e.code}")


# Gmail

def _headers(msg):
    return {h["name"].lower(): h["value"] for h in (msg.get("payload", {}).get("headers") or [])}


def _body_text(payload):
    """Plain text of a message: text/plain if there is one, else text/html stripped."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in ("text/plain", "text/html"):
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if mime == "text/plain" else rich).append(text)
        for p in part.get("parts") or []:
            walk(p)

    walk(payload or {})
    if plain:
        return "\n".join(plain)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(rich))
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def gmail_search(query="is:unread", limit=10):
    limit = max(1, min(int(limit or 10), 25))
    ids = _api("GET", f"{GMAIL}/messages", params={"q": query or "", "maxResults": limit}).get("messages") or []
    out = []
    for item in ids:
        msg = _api("GET", f"{GMAIL}/messages/{item['id']}", params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        h = _headers(msg)
        out.append({"id": msg["id"], "thread": msg.get("threadId"), "from": h.get("from", ""), "subject": h.get("subject", "(no subject)"),
                    "date": h.get("date", ""), "snippet": html.unescape(msg.get("snippet", "")), "unread": "UNREAD" in (msg.get("labelIds") or [])})
    return out


def gmail_read(message_id):
    msg = _api("GET", f"{GMAIL}/messages/{urllib.parse.quote(str(message_id))}", params={"format": "full"})
    h = _headers(msg)
    text = re.sub(r"\n{3,}", "\n\n", _body_text(msg.get("payload")).strip())
    return {"id": msg["id"], "thread": msg.get("threadId"), "from": h.get("from", ""), "to": h.get("to", ""), "cc": h.get("cc", ""),
            "subject": h.get("subject", ""), "date": h.get("date", ""), "message_id": h.get("message-id", ""),
            "text": text[:12000] + ("\n…(cut)" if len(text) > 12000 else "")}


def _raw(to, subject, body, cc="", reply_to_id=""):
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    thread = None
    if reply_to_id:
        original = gmail_read(reply_to_id)
        thread = original["thread"]
        if original.get("message_id"):
            msg["In-Reply-To"] = original["message_id"]
            msg["References"] = original["message_id"]
        if not subject:
            msg.replace_header("Subject", original["subject"] if original["subject"].lower().startswith("re:") else f"Re: {original['subject']}")
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(body)
    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread:
        payload["threadId"] = thread
    return payload


def gmail_draft(to, subject, body, cc="", reply_to_id=""):
    draft = _api("POST", f"{GMAIL}/drafts", {"message": _raw(to, subject, body, cc, reply_to_id)})
    return {"draft_id": draft.get("id"), "note": "Saved as a draft in Gmail; nothing was sent."}


def gmail_send(to, subject, body, cc="", reply_to_id=""):
    sent = _api("POST", f"{GMAIL}/messages/send", _raw(to, subject, body, cc, reply_to_id))
    return {"id": sent.get("id"), "note": "Sent."}


# Calendar

def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ)
    except Exception:
        return datetime.timezone(datetime.timedelta(hours=7))


def _day_bounds(day=None, days=1):
    tz = _tz()
    start = datetime.datetime.combine(day or datetime.datetime.now(tz).date(), datetime.time.min, tzinfo=tz)
    return start.isoformat(), (start + datetime.timedelta(days=max(1, min(int(days or 1), 31)))).isoformat()


def calendar_list(start="", end="", days=1, query=""):
    if not start:
        day = None
        start, end2 = _day_bounds(day, days)
        end = end or end2
    elif not end:
        end = (datetime.datetime.fromisoformat(start) + datetime.timedelta(days=max(1, int(days or 1)))).isoformat()
    params = {"timeMin": start, "timeMax": end, "singleEvents": "true", "orderBy": "startTime", "maxResults": 50, "timeZone": TZ}
    if query:
        params["q"] = query
    items = _api("GET", f"{CALENDAR}/events", params=params).get("items") or []
    out = []
    for e in items:
        s, f = e.get("start", {}), e.get("end", {})
        out.append({"id": e.get("id"), "title": e.get("summary", "(no title)"), "start": s.get("dateTime") or s.get("date"),
                    "end": f.get("dateTime") or f.get("date"), "all_day": "date" in s, "location": e.get("location", ""),
                    "meet": e.get("hangoutLink", ""), "attendees": [a.get("email") for a in e.get("attendees") or []][:15],
                    "description": (e.get("description") or "")[:500]})
    return out


def calendar_create(title, start, end="", description="", location="", attendees=None, all_day=False):
    if all_day:
        day = start[:10]
        last = (end or start)[:10]
        body = {"start": {"date": day}, "end": {"date": (datetime.date.fromisoformat(last) + datetime.timedelta(days=1)).isoformat()}}
    else:
        begin = datetime.datetime.fromisoformat(start)
        if begin.tzinfo is None:
            begin = begin.replace(tzinfo=_tz())
        finish_at = datetime.datetime.fromisoformat(end) if end else begin + datetime.timedelta(hours=1)
        if finish_at.tzinfo is None:
            finish_at = finish_at.replace(tzinfo=_tz())
        body = {"start": {"dateTime": begin.isoformat(), "timeZone": TZ}, "end": {"dateTime": finish_at.isoformat(), "timeZone": TZ}}
    body.update(summary=title, description=description or "", location=location or "")
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    params = {"sendUpdates": "all" if attendees else "none"}
    event = _api("POST", f"{CALENDAR}/events?" + urllib.parse.urlencode(params), body)
    return {"id": event.get("id"), "link": event.get("htmlLink"), "note": "Added to your Google Calendar."}


# The morning brief

BRIEF_PROMPT = (
    "Prepare my morning brief for today, {date}. Use the google tool: today's calendar (calendar_list) and important unread "
    "mail from the last day (gmail_search with 'is:unread newer_than:1d -category:promotions -category:social'). "
    "Use the scheduler tool to list reminders and tasks due today. Then write it for WhatsApp as a title and points only: "
    "first line *Brief · DD-MM-YYYY*, then *Today* (each meeting: time, what, where), *Mail* (who wants what, at most 6, "
    "most important first), *Reminders*, *First* (the one thing to do first). Bullet points, no greeting, no filler, "
    "no closing line, no more than 20 lines. If something could not be read, one point saying so."
)


def _agent_answer(agent_id, prompt):
    from config import conf
    token = str(conf().get("external_api_token") or "")
    port = int(conf().get("web_port") or os.environ.get("WEB_PORT") or 9899)
    day = datetime.datetime.now(_tz()).date().isoformat()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions", method="POST",
        data=json.dumps({"agent_id": agent_id or None, "conversation_id": f"lrm-brief:{agent_id or 'default'}:{day}", "stream": False,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as r:
        body = json.loads(r.read())
    return str(body["choices"][0]["message"]["content"]).strip()


def _send_owner(agent_id, text):
    from config import conf
    token = str(conf().get("external_api_token") or "")
    request = urllib.request.Request(
        f"{GATEWAY_URL}/whatsapp/internal/notify-owner", method="POST",
        data=json.dumps({"agentId": agent_id or "default", "text": text}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as r:
        r.read()


def run_brief(agent_id=None):
    data = load()
    brief = data.get("brief") or {}
    agent_id = agent_id if agent_id is not None else brief.get("agent", "")
    today = datetime.datetime.now(_tz())
    try:
        try:
            text = _agent_answer(agent_id, BRIEF_PROMPT.format(date=today.strftime("%A %d %B %Y")))
        except Exception as e:
            raise GoogleError(f"the agent could not prepare it ({e}); check that its model has a key and credit")
        try:
            _send_owner(agent_id, f"☀️ Morning brief\n\n{text}")
        except Exception as e:
            raise GoogleError(f"it could not be sent ({e}); check that a WhatsApp number is linked")
        brief.update(last_run=today.isoformat(timespec="minutes"), last_error="")
        return text
    except Exception as e:
        brief.update(last_run=today.isoformat(timespec="minutes"), last_error=str(e)[:300])
        logger.warning(f"[MorningBrief] failed: {e}")
        raise
    finally:
        update(brief={**(load().get("brief") or {}), **brief})


def _brief_loop():
    while True:
        try:
            brief = load().get("brief") or {}
            if brief.get("enabled"):
                now = datetime.datetime.now(_tz())
                hh, mm = (brief.get("time") or "07:00").split(":")
                due = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                done_today = str(brief.get("last_run", "")).startswith(now.date().isoformat())
                if now >= due and not done_today and (now - due).total_seconds() < 3 * 3600:
                    threading.Thread(target=lambda: _safe(run_brief), daemon=True).start()
                    update(brief={**brief, "last_run": now.isoformat(timespec="minutes")})
        except Exception as e:
            logger.warning(f"[MorningBrief] scheduler: {e}")
        time.sleep(30)


def _safe(fn):
    try:
        fn()
    except Exception:
        pass


_started = False


def start_scheduler():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_brief_loop, name="lrm-morning-brief", daemon=True).start()
