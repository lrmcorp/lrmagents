"""/api/lrm/shortcut: the owner's iPhone (Apple Shortcuts) reads the daily brief and talks to Sora.

GET  ?op=brief                -> {text, at, task}   latest successful "Ringkasan Operasional…" run
POST ?op=ask   {text, lang?}  -> {text}             one turn with Sora (the Personal page's voice chat)
POST ?op=ask_audio {text, lang?} -> audio/mpeg     ask and speak in one request (fastest for Siri and Back Tap)
POST ?op=tts   {text}         -> audio/mpeg         Sora's voice for a text
POST ?op=brief_audio          -> audio/mpeg         the brief, read aloud

Auth is a long shared token in the X-LRM-Token header (LRMAGENT_SHORTCUT_TOKEN), not the console
cookie: Shortcuts cannot sign in. With no token configured the endpoint does not exist (404).
Installed as channel/web/api/lrm_shortcut.py by the LRMagent image.
"""
import datetime
import hmac
import json
import os
import re
import threading
import time
import urllib.error
from urllib.parse import parse_qs

import web

from common.log import logger

MIN_TOKEN = 20
MAX_FAILURES = 10
WINDOW = 600
ASK_MAX = 2000
TTS_MAX = 1500
# The voice service's own text cap (voice/server.py MAX_TEXT); it reads past Google's limit with Kokoro.
BRIEF_AUDIO_MAX = 4000
BRIEF_PREFIX = "ringkasan operasional"
NO_BRIEF = "Belum ada ringkasan operasional."
_WIB = datetime.timezone(datetime.timedelta(hours=7))

_failures = {}
_lock = threading.Lock()


def _json(status, body):
    web.ctx.status = status
    web.header("Content-Type", "application/json; charset=utf-8")
    return json.dumps(body, ensure_ascii=False)


def _expected_token():
    token = os.environ.get("LRMAGENT_SHORTCUT_TOKEN", "").strip()
    return token if len(token) >= MIN_TOKEN else ""


def _client():
    try:
        from channel.web.core._common import _client_ip
        return _client_ip()
    except Exception:
        env = getattr(web.ctx, "env", {}) or {}
        forwarded = (env.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        return forwarded or (env.get("REMOTE_ADDR") or "").strip() or "unknown"


def _recent(client, now):
    stamps = [t for t in _failures.get(client, ()) if now - t < WINDOW]
    if stamps:
        _failures[client] = stamps
    else:
        _failures.pop(client, None)
    return stamps


def _note_failure(client, now):
    with _lock:
        # Bounded memory: a flood of distinct addresses cannot grow the table forever.
        if len(_failures) > 5000:
            for key in [k for k, v in _failures.items() if not v or now - v[-1] >= WINDOW]:
                _failures.pop(key, None)
        _failures.setdefault(client, []).append(now)


def _authorize():
    """None when the caller may proceed, else the response to send."""
    expected = _expected_token()
    if not expected:
        return _json("404 Not Found", {"error": "not found"})
    client = _client()
    now = time.time()
    with _lock:
        stamps = _recent(client, now)
    if len(stamps) >= MAX_FAILURES:
        web.header("Retry-After", str(max(1, int(WINDOW - (now - stamps[-MAX_FAILURES])))))
        return _json("429 Too Many Requests", {"error": "too many wrong tokens; try again later"})
    offered = (web.ctx.env.get("HTTP_X_LRM_TOKEN") or "").strip()
    if offered and hmac.compare_digest(offered.encode(), expected.encode()):
        return None
    _note_failure(client, now)
    logger.warning(f"[Shortcut] wrong or missing token from {client}")
    return _json("401 Unauthorized", {"error": "wrong token"})


_NOTE = re.compile(r"\n\s*\n(?:Delivered|Not delivered)\b[\s\S]*$")
_MENTION_NOTE = re.compile(r"\s\[(?:tagged|skipped) [^\]\n]*\]\s*$")
_BOLD = re.compile(r"(?<![\w*])\*{1,2}(?=\S)([^*\n]+?)(?<=\S)\*{1,2}(?![\w*])")
_ITALIC = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])")


def clean_brief(text):
    """A run's delivered text, ready to read aloud: no History notes, send markers or WhatsApp styling."""
    text = str(text or "").replace("\r\n", "\n")
    text = _NOTE.sub("", text)
    text = _MENTION_NOTE.sub("", text)
    text = text.replace("[KIRIM]", "").replace("[/KIRIM]", "")
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _STRIKE.sub(r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _store():
    from agent.memory import get_conversation_store
    return get_conversation_store()


def _run_text(store, row):
    # The runs row keeps only a 1000-character preview; the full delivered text sits in the receiver's session.
    try:
        detail = store.get_run_detail(row["run_id"]) or {}
        if detail.get("full_output"):
            return str(detail["full_output"])
    except Exception:
        pass
    return str((row.get("extras") or {}).get("output_preview") or "")


def latest_brief(store=None):
    store = store if store is not None else _store()
    if store is not None:
        for page in range(10):
            rows = store.list_runs(task_source="scheduler", limit=200, offset=page * 200)
            for row in rows:
                name = str((row.get("extras") or {}).get("task_name") or "").strip()
                if row.get("status") != "done" or not name.lower().startswith(BRIEF_PREFIX):
                    continue
                text = clean_brief(_run_text(store, row))
                if not text:
                    continue  # a run that chose to send nothing
                ended = row.get("ended_at") or row.get("started_at")
                at = datetime.datetime.fromtimestamp(int(ended), _WIB).strftime("%d-%m-%Y %H:%M") if ended else ""
                return {"text": text, "at": at, "task": name}
            if len(rows) < 200:
                break
    return {"text": NO_BRIEF, "at": "", "task": ""}


def _voice_api():
    from channel.web.api import lrm_voice
    return lrm_voice


def _speak(text, lang=None):
    req = {"text": text, "format": "mp3", "engine": "google"}
    if lang:
        req["lang"] = lang
    body, headers = _voice_api()._voice("/tts", data=json.dumps(req).encode(),
                                        headers={"Content-Type": "application/json"}, timeout=300)
    web.header("Content-Type", (headers or {}).get("Content-Type") or "audio/mpeg")
    return body


def _ask_audio(text, lang):
    started = time.time()
    answer = _voice_api().chat(text, None, lang, spoken_only=True)["text"][:TTS_MAX] or "Maaf, saya tidak punya jawaban."
    thought = time.time() - started
    audio = _speak(answer, lang)
    logger.info(f"[Shortcut] ask_audio: model {thought:.1f}s, voice {time.time() - started - thought:.1f}s, "
                f"{len(text)} chars in, {len(answer)} out, {len(audio) // 1024} KB")
    return audio


def _op():
    return (parse_qs(web.ctx.env.get("QUERY_STRING", "")).get("op") or [""])[0]


def _body():
    try:
        data = json.loads(web.data() or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _guarded(op, work):
    service = "the assistant" if op in ("ask", "ask_audio") else "the voice service"
    try:
        return work()
    except urllib.error.HTTPError as e:
        logger.warning(f"[Shortcut] {op}: upstream answered {e.code}")
        return _json("502 Bad Gateway", {"error": f"{service} answered {e.code}"})
    except urllib.error.URLError as e:
        logger.warning(f"[Shortcut] {op}: {e.reason}")
        return _json("503 Service Unavailable", {"error": f"{service} is not reachable"})
    except Exception as e:
        logger.warning(f"[Shortcut] {op} failed: {type(e).__name__}: {e}")
        return _json("503 Service Unavailable", {"error": str(e)[:300] or type(e).__name__})


class LrmShortcutHandler:
    def GET(self):
        denied = _authorize()
        if denied is not None:
            return denied
        op = _op()
        if op == "brief":
            return _guarded(op, lambda: _json("200 OK", latest_brief()))
        if op in ("ask", "ask_audio", "tts", "brief_audio"):
            return _json("405 Method Not Allowed", {"error": f"use POST for op={op}"})
        return _json("404 Not Found", {"error": "unknown op"})

    def POST(self):
        denied = _authorize()
        if denied is not None:
            return denied
        op = _op()
        if op == "brief_audio":
            return _guarded(op, lambda: _speak(latest_brief()["text"][:BRIEF_AUDIO_MAX]))
        if op not in ("ask", "ask_audio", "tts"):
            if op == "brief":
                return _json("405 Method Not Allowed", {"error": "use GET for op=brief"})
            return _json("404 Not Found", {"error": "unknown op"})
        req = _body()
        if req is None:
            return _json("400 Bad Request", {"error": "send a JSON object"})
        text = str(req.get("text") or "").strip()
        limit = TTS_MAX if op == "tts" else ASK_MAX
        if not text:
            return _json("400 Bad Request", {"error": "text is required"})
        if len(text) > limit:
            return _json("413 Payload Too Large", {"error": f"text is longer than {limit} characters"})
        if op == "tts":
            return _guarded(op, lambda: _speak(text))
        lang = req.get("lang") if isinstance(req.get("lang"), str) and req.get("lang") else None
        if op == "ask_audio":
            return _guarded(op, lambda: _ask_audio(text, lang))
        return _guarded(op, lambda: _json("200 OK", {"text": _voice_api().chat(text, None, lang, spoken_only=True)["text"]}))
