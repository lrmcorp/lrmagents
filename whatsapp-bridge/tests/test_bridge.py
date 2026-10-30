import hashlib
import hmac
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge  # noqa: E402

SECRET = "app-secret"
ENV = {
    "WHATSAPP_ACCESS_TOKEN": "graph-token", "WHATSAPP_PHONE_NUMBER_ID": "111",
    "WHATSAPP_APP_SECRET": SECRET, "WHATSAPP_VERIFY_TOKEN": "verify-me",
    "WHATSAPP_ALLOWED_NUMBERS": "+62 812-3456-7890, 6281111111111",
    "COWAGENT_API_TOKEN": "agent-token", "COWAGENT_URL": "http://agent.test",
}


def payload(sender="6281234567890", message_id="wamid.1", text="hello", kind="text"):
    message = {"from": sender, "id": message_id, "type": kind}
    if kind == "text":
        message["text"] = {"body": text}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}]}


def sign(body):
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class FakeHTTP:
    """Records outgoing calls; answers like CowAgent and the Graph API."""

    def __init__(self, agent_reply="Hi from LRM Agents", fail_agent=False):
        self.calls, self.agent_reply, self.fail_agent = [], agent_reply, fail_agent
        self.done = threading.Event()

    def __call__(self, url, data, token, timeout):
        self.calls.append((url, data, token))
        if url.endswith("/v1/chat/completions"):
            if self.fail_agent:
                raise RuntimeError("agent down")
            return {"choices": [{"message": {"role": "assistant", "content": self.agent_reply}}]}
        if data.get("type") == "text":
            self.done.set()
        return {"messages": [{"id": "wamid.out"}]}

    def sent_texts(self):
        return [d["text"]["body"] for u, d, _ in self.calls if d.get("type") == "text"]


def wait(fake):
    assert fake.done.wait(5), "no reply sent"
    time.sleep(0.05)


class UnitTests(unittest.TestCase):
    def test_signature(self):
        body = b'{"a":1}'
        self.assertTrue(bridge.valid_signature(SECRET, body, sign(body)))
        self.assertFalse(bridge.valid_signature(SECRET, body, sign(b'{"a":2}')))
        self.assertFalse(bridge.valid_signature(SECRET, body, None))
        self.assertFalse(bridge.valid_signature("", body, sign(body)))

    def test_allowlist_normalises_formatting(self):
        settings = bridge.Settings(ENV)
        self.assertEqual(settings.allowed, {"6281234567890", "6281111111111"})
        self.assertTrue(settings.configured)
        self.assertFalse(bridge.Settings({**ENV, "WHATSAPP_ALLOWED_NUMBERS": ""}).configured)

    def test_status_updates_yield_nothing(self):
        status_only = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
        self.assertEqual(list(bridge.incoming_messages(status_only)), [])

    def test_chunks_respect_limit_and_keep_text(self):
        text = ("word " * 2000).strip()
        parts = list(bridge.chunks(text, 100))
        self.assertTrue(all(len(p) <= 100 for p in parts))
        self.assertEqual(" ".join(parts), text)

    def test_answers_with_per_number_conversation(self):
        fake = FakeHTTP()
        b = bridge.Bridge(bridge.Settings(ENV), post=fake)
        self.assertEqual(b.handle(payload()), 1)
        wait(fake)
        agent_call = next(c for c in fake.calls if c[0].endswith("/v1/chat/completions"))
        self.assertEqual(agent_call[1]["conversation_id"], "whatsapp:6281234567890")
        self.assertEqual(agent_call[2], "agent-token")
        self.assertEqual(fake.sent_texts(), ["Hi from LRM Agents"])
        graph = [c for c in fake.calls if "graph.facebook.com/v23.0/111/messages" in c[0]]
        self.assertTrue(graph and all(c[2] == "graph-token" for c in graph))

    def test_duplicate_delivery_answered_once(self):
        fake = FakeHTTP()
        b = bridge.Bridge(bridge.Settings(ENV), post=fake)
        self.assertEqual(b.handle(payload()), 1)
        self.assertEqual(b.handle(payload()), 0)

    def test_stranger_is_ignored(self):
        fake = FakeHTTP()
        b = bridge.Bridge(bridge.Settings(ENV), post=fake)
        self.assertEqual(b.handle(payload(sender="15550001111")), 0)
        time.sleep(0.1)
        self.assertEqual(fake.calls, [])

    def test_non_text_gets_explanation_without_agent(self):
        fake = FakeHTTP()
        b = bridge.Bridge(bridge.Settings(ENV), post=fake)
        b.handle(payload(kind="image"))
        wait(fake)
        self.assertEqual(fake.sent_texts(), [bridge.UNSUPPORTED_REPLY])
        self.assertFalse(any(c[0].endswith("/v1/chat/completions") for c in fake.calls))

    def test_agent_failure_sends_apology(self):
        fake = FakeHTTP(fail_agent=True)
        b = bridge.Bridge(bridge.Settings(ENV), post=fake)
        b.handle(payload())
        wait(fake)
        self.assertEqual(fake.sent_texts(), [bridge.ERROR_REPLY])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHTTP()
        handler = bridge.make_handler(bridge.Bridge(bridge.Settings(ENV), post=self.fake))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()

    def request(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=body, headers=headers or {},
                                     method="POST" if body is not None else "GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_verification_handshake(self):
        ok = self.request("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42")
        self.assertEqual(ok, (200, b"42"))
        bad = self.request("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=42")
        self.assertEqual(bad[0], 403)

    def test_signed_post_is_answered(self):
        body = json.dumps(payload()).encode()
        status, _ = self.request("/whatsapp/webhook", body, {"X-Hub-Signature-256": sign(body),
                                                             "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        wait(self.fake)
        self.assertEqual(self.fake.sent_texts(), ["Hi from LRM Agents"])

    def test_unsigned_post_rejected(self):
        body = json.dumps(payload()).encode()
        status, _ = self.request("/whatsapp/webhook", body, {"X-Hub-Signature-256": "sha256=00"})
        self.assertEqual(status, 401)
        time.sleep(0.1)
        self.assertEqual(self.fake.calls, [])

    def test_health(self):
        status, body = self.request("/whatsapp/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"configured": True, "allowed_numbers": 2})


if __name__ == "__main__":
    unittest.main()
