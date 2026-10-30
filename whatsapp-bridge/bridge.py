"""WhatsApp Cloud API <-> LRM Agents (CowAgent) bridge.

Meta delivers WhatsApp messages to POST /whatsapp/webhook. Each signed text
message from an allowlisted number is answered by CowAgent's OpenAI-compatible
API, with one server-side conversation per WhatsApp number, and the reply is
sent back through the Cloud API. Standard library only.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("whatsapp-bridge")

WEBHOOK_PATH = "/whatsapp/webhook"
HEALTH_PATH = "/whatsapp/health"
MAX_BODY_BYTES = 1024 * 1024
# WhatsApp caps a text body at 4096 characters.
MAX_TEXT = 4000
UNSUPPORTED_REPLY = "I can only read text messages for now. Please type your question."
ERROR_REPLY = "Sorry, something went wrong on my side. Please try again in a moment."


def _digits(number):
    return re.sub(r"\D", "", number or "")


class Settings:
    def __init__(self, env=os.environ):
        self.access_token = env.get("WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = env.get("WHATSAPP_PHONE_NUMBER_ID", "")
        self.app_secret = env.get("WHATSAPP_APP_SECRET", "")
        self.verify_token = env.get("WHATSAPP_VERIFY_TOKEN", "")
        self.allowed = {_digits(n) for n in env.get("WHATSAPP_ALLOWED_NUMBERS", "").split(",") if _digits(n)}
        self.graph_base = env.get("WHATSAPP_GRAPH_BASE", "https://graph.facebook.com").rstrip("/")
        self.graph_version = env.get("WHATSAPP_GRAPH_VERSION", "v23.0")
        self.agent_url = env.get("COWAGENT_URL", "http://cowagent:9899").rstrip("/")
        self.agent_token = env.get("COWAGENT_API_TOKEN", "")
        self.agent_timeout = float(env.get("COWAGENT_TIMEOUT", "600"))
        self.port = int(env.get("PORT", "8080"))

    @property
    def configured(self):
        return all((self.access_token, self.phone_number_id, self.app_secret,
                    self.verify_token, self.agent_token, self.allowed))


def valid_signature(app_secret, body, header):
    """Meta signs the raw body with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def incoming_messages(payload):
    """(sender, message id, text or None) for each user message in a webhook payload.

    Status updates (sent/delivered/read) carry no messages and yield nothing.
    """
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                sender, message_id = message.get("from"), message.get("id")
                if not sender or not message_id:
                    continue
                text = None
                if message.get("type") == "text":
                    text = (message.get("text") or {}).get("body") or None
                yield sender, message_id, text


def chunks(text, size=MAX_TEXT):
    """Split a reply into WhatsApp-sized parts, preferring paragraph then line breaks."""
    text = text.strip()
    while len(text) > size:
        cut = text.rfind("\n\n", 0, size)
        if cut < size // 2:
            cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        yield text[:cut].rstrip()
        text = text[cut:].lstrip()
    if text:
        yield text


class SeenIds:
    """Meta redelivers webhooks it thinks failed; answer each message id once."""

    def __init__(self, limit=5000):
        self._ids, self._limit, self._lock = OrderedDict(), limit, threading.Lock()

    def first_time(self, message_id):
        with self._lock:
            if message_id in self._ids:
                return False
            self._ids[message_id] = None
            if len(self._ids) > self._limit:
                self._ids.popitem(last=False)
            return True


def _post_json(url, payload, token, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


class Bridge:
    def __init__(self, settings, post=_post_json):
        self.settings = settings
        self.post = post
        self.seen = SeenIds()
        # One worker per conversation would be ideal; a small pool keeps a burst
        # from one number from starving the rest. CowAgent serialises each session.
        self.pool = ThreadPoolExecutor(max_workers=4)

    def _graph(self, payload):
        s = self.settings
        url = f"{s.graph_base}/{s.graph_version}/{s.phone_number_id}/messages"
        return self.post(url, payload, s.access_token, 30)

    def send_text(self, to, text):
        for part in chunks(text):
            self._graph({"messaging_product": "whatsapp", "recipient_type": "individual",
                         "to": to, "type": "text", "text": {"preview_url": False, "body": part}})

    def mark_read(self, message_id):
        """Blue ticks plus a typing indicator while the agent works; cosmetic, so failures are ignored."""
        try:
            self._graph({"messaging_product": "whatsapp", "status": "read", "message_id": message_id,
                         "typing_indicator": {"type": "text"}})
        except Exception as error:  # noqa: BLE001
            log.info("mark_read failed: %s", error)

    def ask_agent(self, sender, text):
        s = self.settings
        reply = self.post(f"{s.agent_url}/v1/chat/completions",
                          {"messages": [{"role": "user", "content": text}],
                           "conversation_id": f"whatsapp:{sender}", "stream": False},
                          s.agent_token, s.agent_timeout)
        content = (((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return content or "(No reply.)"

    def handle(self, payload):
        """Queue every new message in a verified payload; returns how many were queued."""
        queued = 0
        for sender, message_id, text in incoming_messages(payload):
            if _digits(sender) not in self.settings.allowed:
                log.warning("ignored message from non-allowlisted number ending %s", sender[-4:])
                continue
            if not self.seen.first_time(message_id):
                continue
            self.pool.submit(self._answer, sender, message_id, text)
            queued += 1
        return queued

    def _answer(self, sender, message_id, text):
        started = time.monotonic()
        try:
            self.mark_read(message_id)
            if text is None:
                self.send_text(sender, UNSUPPORTED_REPLY)
                return
            self.send_text(sender, self.ask_agent(sender, text))
            log.info("answered ...%s in %.1fs", sender[-4:], time.monotonic() - started)
        except Exception as error:  # noqa: BLE001
            detail = error.read()[:300] if isinstance(error, urllib.error.HTTPError) else ""
            log.error("failed to answer ...%s: %s %s", sender[-4:], error, detail)
            try:
                self.send_text(sender, ERROR_REPLY)
            except Exception:  # noqa: BLE001
                pass


def make_handler(bridge):
    settings = bridge.settings

    class Handler(BaseHTTPRequestHandler):
        server_version = "lrm-whatsapp-bridge"

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def _reply(self, status, body=b"", content_type="text/plain"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == HEALTH_PATH:
                state = {"configured": settings.configured, "allowed_numbers": len(settings.allowed)}
                return self._reply(200, json.dumps(state).encode(), "application/json")
            if url.path != WEBHOOK_PATH:
                return self._reply(404)
            # Meta's subscription handshake when the webhook URL is saved.
            query = parse_qs(url.query)
            mode = query.get("hub.mode", [""])[0]
            token = query.get("hub.verify_token", [""])[0]
            challenge = query.get("hub.challenge", [""])[0]
            if mode == "subscribe" and settings.verify_token and hmac.compare_digest(token, settings.verify_token):
                return self._reply(200, challenge.encode())
            return self._reply(403)

        def do_POST(self):
            if urlparse(self.path).path != WEBHOOK_PATH:
                return self._reply(404)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                return self._reply(413 if length else 400)
            body = self.rfile.read(length)
            if not valid_signature(settings.app_secret, body, self.headers.get("X-Hub-Signature-256")):
                return self._reply(401)
            try:
                payload = json.loads(body)
            except ValueError:
                return self._reply(400)
            # Acknowledge at once; Meta retries deliveries that take too long.
            if settings.configured:
                bridge.handle(payload)
            self._reply(200, b"ok")

    return Handler


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    if not settings.configured:
        log.warning("WhatsApp is not fully configured; incoming messages will be ignored.")
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), make_handler(Bridge(settings)))
    log.info("listening on :%d%s", settings.port, WEBHOOK_PATH)
    server.serve_forever()


if __name__ == "__main__":
    main()
