"""/api/lrm/voice: the console's Personal page (the orb) talks to the personal assistant.

GET                       -> {agent: {id, name}, voice: {...health}}
POST ?op=stt   (audio)    -> {text, language}           speech to text (voice service)
POST ?op=tts   {text,lang}-> audio/wav                   the assistant's voice (voice service)
POST ?op=chat  {text, image?, lang?} -> {text, speech, hud, agent}
     One turn with the personal agent in today's voice conversation; an image (a camera
     frame, data URL) is saved in its workspace and handed to its vision tool.
Signed-in console only. Installed as channel/web/api/lrm_voice.py by the LRMagent image.
"""
import base64
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request

import web

from channel.web.core._common import _require_auth
from common.log import logger

VOICE_URL = os.environ.get("LRMAGENT_VOICE_URL", "http://voice:8095").rstrip("/")


def _token():
    from config import conf
    return str(conf().get("external_api_token") or "")


def _voice(path, data=None, headers=None, timeout=180):
    request = urllib.request.Request(f"{VOICE_URL}{path}", data=data, method="POST" if data is not None else "GET",
                                     headers={"Authorization": f"Bearer {_token()}", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return r.read(), r.headers


def _json(status, body):
    web.ctx.status = status
    web.header("Content-Type", "application/json; charset=utf-8")
    return json.dumps(body, ensure_ascii=False)


HUD = re.compile(r"```hud\s*(.*?)```", re.S)


def split_hud(text):
    """(spoken text, panels) from an answer that may end with a ```hud block."""
    panels = []
    for raw in HUD.findall(text or ""):
        try:
            data = json.loads(raw)
            panels += data if isinstance(data, list) else [data]
        except ValueError:
            continue
    spoken = HUD.sub("", text or "").strip()
    clean = []
    for p in panels[:12]:
        if isinstance(p, dict) and p.get("type") in ("stat", "chart", "list", "image", "text"):
            clean.append(p)
    return spoken, clean


def _save_image(data_url, ws):
    m = re.match(r"data:image/(jpeg|png|webp);base64,(.+)$", data_url or "", re.S)
    if not m:
        return None
    raw = base64.b64decode(m.group(2))
    if len(raw) > 6 << 20:
        raise ValueError("image too large")
    folder = os.path.join(ws, "tmp", "camera")
    os.makedirs(folder, exist_ok=True)
    for name in sorted(os.listdir(folder))[:-40]:  # keep the last 40 frames
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    path = os.path.join(folder, f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.{m.group(1).replace('jpeg', 'jpg')}")
    with open(path, "wb") as f:
        f.write(raw)
    return path


def chat(text, image=None, lang=None):
    from agent import lrm_personal
    from config import conf
    lrm_personal.ensure_agent()
    profile = lrm_personal.profile()
    if profile is None:
        raise RuntimeError("the personal assistant does not exist yet")
    parts = ["[Voice conversation on the Personal page: answer in one to three short spoken sentences; put details in a ```hud block.]"]
    if lang:
        parts.append(f"[The owner spoke {({'id': 'Bahasa Indonesia', 'zh': 'Mandarin', 'en': 'English'}).get(lang, lang)}.]")
    if image:
        path = _save_image(image, profile.workspace)
        if path:
            parts.append(f"[The owner is showing you their camera; the frame is saved at {path}. Look at it with the vision tool.]")
    parts.append(text or "What do you see?")
    port = int(conf().get("web_port") or os.environ.get("WEB_PORT") or 9899)
    day = datetime.date.today().isoformat()
    body = {"agent_id": profile.id, "conversation_id": f"lrm-voice:{profile.id}:{day}", "stream": False,
            "messages": [{"role": "user", "content": "\n".join(parts)}]}
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", method="POST", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=900) as r:
            answer = str(json.loads(r.read())["choices"][0]["message"]["content"]).strip()
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read() or b"{}").get("error", {}).get("message")
        except ValueError:
            why = None
        raise RuntimeError(why or f"the assistant answered {e.code}")
    spoken, panels = split_hud(answer)
    return {"text": spoken, "hud": panels, "agent": {"id": profile.id, "name": profile.name}}


class LrmVoiceHandler:
    def GET(self):
        _require_auth()
        from agent import lrm_personal
        lrm_personal.ensure_agent()
        p = lrm_personal.profile()
        try:
            health = json.loads(_voice("/health", timeout=5)[0])
        except Exception as e:
            health = {"ok": False, "error": str(e)[:120]}
        return _json("200 OK", {"agent": {"id": p.id, "name": p.name} if p else None, "voice": health})

    def POST(self):
        _require_auth()
        # The query string only: web.input() would parse the body, and audio is not form text.
        from urllib.parse import parse_qs
        op = (parse_qs(web.ctx.env.get("QUERY_STRING", "")).get("op") or [""])[0]
        started = time.time()
        try:
            if op == "stt":
                data = web.data()
                if not data or len(data) > 25 << 20:
                    return _json("400 Bad Request", {"error": "send 1 byte to 25 MB of audio"})
                lang = web.ctx.env.get("HTTP_X_LANG") or ""
                body, _ = _voice("/stt", data=data, headers={"Content-Type": "application/octet-stream", **({"X-Lang": lang} if lang else {})}, timeout=300)
                return _json("200 OK", json.loads(body))
            req = json.loads(web.data() or b"{}")
            if op == "tts":
                body, headers = _voice("/tts", data=json.dumps({"text": str(req.get("text") or "")[:4000], "lang": req.get("lang"),
                                                                "voice": req.get("voice"), "format": "wav"}).encode(),
                                       headers={"Content-Type": "application/json"}, timeout=300)
                web.header("Content-Type", "audio/wav")
                web.header("X-Lang", headers.get("X-Lang") or "")
                return body
            if op == "chat":
                result = chat(str(req.get("text") or "").strip()[:4000], req.get("image"), req.get("lang"))
                result["seconds"] = round(time.time() - started, 1)
                return _json("200 OK", result)
            return _json("404 Not Found", {"error": "unknown op"})
        except urllib.error.HTTPError as e:
            try:
                why = json.loads(e.read() or b"{}").get("error")
            except ValueError:
                why = None
            return _json("502 Bad Gateway", {"error": f"voice service: {why or e.code}"})
        except urllib.error.URLError as e:
            return _json("503 Service Unavailable", {"error": f"The voice service is not reachable ({e.reason})."})
        except Exception as e:
            logger.warning(f"[Voice] {op} failed: {e}")
            return _json("500 Internal Server Error", {"error": str(e)[:300]})


# The personal assistant exists from the first start, so it shows up on the Agents page.
try:
    from agent import lrm_personal as _p
    _p.ensure_agent()
except Exception as _e:  # never stop the console from starting
    logger.warning(f"[Personal] not created at start: {_e}")
