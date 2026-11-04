"""POST /api/lrm/tts {text} -> audio/ogg (Opus): a WhatsApp voice note of the text.

For the WhatsApp gateway's voice replies, from inside the Docker network with the
internal API token. Uses the console's text-to-speech provider (Config → Voice), else LRMagent's own
voice (Kokoro in the voice service); 204 when neither answers, and the gateway sends text only.
Installed as channel/web/api/lrm_tts.py by the LRMagent image.
"""

import hmac
import json
import os
import subprocess
import tempfile

import web

from common.log import logger


def _internal_caller():
    from config import conf
    token = str(conf().get("external_api_token") or "")
    given = web.ctx.env.get("HTTP_AUTHORIZATION", "").replace("Bearer ", "", 1).strip()
    return bool(token) and not web.ctx.env.get("HTTP_X_FORWARDED_FOR") and hmac.compare_digest(given, token)


def _local_voice(text):
    import urllib.request
    from config import conf
    url = os.environ.get("LRMAGENT_VOICE_URL", "http://voice:8095").rstrip("/")
    request = urllib.request.Request(f"{url}/tts", method="POST", data=json.dumps({"text": text, "format": "ogg"}).encode(),
                                     headers={"Authorization": f"Bearer {conf().get('external_api_token') or ''}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as r:
            return r.read()
    except Exception as e:
        logger.info(f"[TTS] local voice unavailable: {e}")
        return b""


class LrmTtsHandler:
    def POST(self):
        if not _internal_caller():
            raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"}, json.dumps({"error": "internal endpoint"}))
        try:
            text = str(json.loads(web.data() or b"{}").get("text") or "").strip()[:1500]
        except ValueError:
            text = ""
        if not text:
            web.ctx.status = "204 No Content"
            return b""
        try:
            from bridge.bridge import Bridge
            from bridge.reply import ReplyType
            reply = None
            try:
                reply = Bridge().fetch_text_to_voice(text)
            except Exception as e:
                logger.info(f"[TTS] console voice provider unavailable: {e}")
            if reply is None or reply.type != ReplyType.VOICE or not reply.content or not os.path.isfile(str(reply.content)):
                # No voice provider set up: LRMagent's own voice (Kokoro, voice service).
                data = _local_voice(text)
                if not data:
                    web.ctx.status = "204 No Content"
                    return b""
                web.header("Content-Type", "audio/ogg; codecs=opus")
                return data
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "voice.ogg")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(reply.content), "-ac", "1", "-ar", "48000",
                                "-c:a", "libopus", "-b:a", "32k", "-application", "voip", out], check=True, timeout=60)
                with open(out, "rb") as f:
                    data = f.read()
            web.header("Content-Type", "audio/ogg; codecs=opus")
            return data
        except Exception as e:
            logger.info(f"[TTS] voice reply skipped: {e}")
            web.ctx.status = "204 No Content"
            return b""
