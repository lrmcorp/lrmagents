"""Stubbed tests for the WhatsApp relay's delivery notes and @number tags (python3 test_whatsapp_relay.py)."""

import io
import json
import os
import sys
import types
import urllib.error
import urllib.request


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("config").conf = lambda: {"external_api_token": "t"}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import whatsapp_relay as wr  # noqa: E402

PASS = {"f": 0}


def check(name, got, want):
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# chat suffix -> "ok" | "refused" | "offline"
ANSWER = {}
STATUS = {}
SENT = []       # (chat, text) posted to /send
OWNER = []      # texts posted to /notify-owner


def _urlopen(req, timeout=30):
    if req.full_url.endswith("/status"):
        return _Resp(json.dumps(STATUS).encode())
    if req.full_url.endswith("/notify-owner"):
        OWNER.append(json.loads(req.data)["text"])
        return _Resp(b"{}")
    chat = json.loads(req.data)["chat"]
    SENT.append((chat, json.loads(req.data)["text"]))
    how = next((v for k, v in ANSWER.items() if chat.endswith(k)), "ok")
    if how == "refused":
        raise urllib.error.HTTPError(req.full_url, 403, "no", {}, io.BytesIO(b'{"error":"not a designated contact"}'))
    if how == "offline":
        raise OSError("phone offline")
    return _Resp(b"{}")


urllib.request.urlopen = _urlopen
P = wr.SESSION_PREFIX + "default:"
reply = types.SimpleNamespace(content="Laporan cabang")

# (a) a single-recipient refusal is final but noted, not shown as delivered.
ANSWER.clear()
ANSWER["111@s.whatsapp.net"] = "refused"
relay = wr.relay_for(P + "628111@s.whatsapp.net")
relay.send(reply)
check("single refusal noted", "refused it (not a designated contact)" in relay.delivery_note, True)
ANSWER.clear()
relay = wr.relay_for(P + "628111@s.whatsapp.net")
relay.send(reply)
check("single ok, no note", relay.delivery_note, "")

# (b) several recipients, one offline: no raise (that re-runs the agent), but noted clearly.
ANSWER.clear()
ANSWER["222@s.whatsapp.net"] = "offline"
fan = wr.FanOut(None, {"receiver": P + "628111@s.whatsapp.net", "receivers": [P + "628222@s.whatsapp.net"]})
fan.send(reply)
check("partial transient noted", "Not delivered to 628222@s.whatsapp.net, and not retried" in fan.delivery_note, True)
check("partial still lists the delivered one", "628111@s.whatsapp.net ✓" in fan.delivery_note, True)
# every recipient refused: final, so no raise (a retry cannot help), and History says so.
ANSWER.clear()
ANSWER["111@s.whatsapp.net"] = ANSWER["222@s.whatsapp.net"] = "refused"
fan = wr.FanOut(None, {"receiver": P + "628111@s.whatsapp.net", "receivers": [P + "628222@s.whatsapp.net"]})
fan.send(reply)
check("all refused, no raise", fan.delivery_note.count("✗ (not a designated contact)"), 2)
# a single recipient offline: raises, so the scheduler keeps the task for the next tick.
ANSWER.clear()
ANSWER["111@s.whatsapp.net"] = "offline"
try:
    wr.relay_for(P + "628111@s.whatsapp.net").send(reply)
    check("single offline raises", False, True)
except Exception:
    check("single offline raises", True, True)
ANSWER.clear()
ANSWER["222@s.whatsapp.net"] = "offline"
# nobody reached and offline: still raises so the run is retried.
ANSWER["111@s.whatsapp.net"] = "offline"
fan = wr.FanOut(None, {"receiver": P + "628111@s.whatsapp.net", "receivers": [P + "628222@s.whatsapp.net"]})
try:
    fan.send(reply)
    check("all offline raises", False, True)
except RuntimeError:
    check("all offline raises", True, True)

# (c) @number tags from text: emails, order ids and local 0… numbers are not tagged.
act = {"mention_from_text": True}
check("tag real number", wr._mentions_for(act, "Tolong cek @6281234567890 ya"), ["+6281234567890"])
check("tag +number", wr._mentions_for(act, "cc @+6281234567890."), ["+6281234567890"])
check("email not tagged", wr._mentions_for(act, "kirim ke budi@6281234567890"), [])
check("order id not tagged", wr._mentions_for(act, "order@20261119001 selesai"), [])
check("dot before @ not tagged", wr._mentions_for(act, "x.@6281234567890"), [])
check("local 0 number not tagged", wr._mentions_for(act, "hubungi @081234567890"), [])
check("off by default", wr._mentions_for({}, "@6281234567890"), [])

# (d) the *LAPORAN rule for scheduled AI reports (owner +64225802322, one designated contact).
ANSWER.clear()
STATUS.update({"accounts": [{"agent": "default", "status": "linked", "phone": "6281000", "ownerNumbers": ["64225802322"]}],
               "designated": [{"name": "Reyner", "phone": "628111"}]})
OWN, REY = P + "64225802322@s.whatsapp.net", P + "628111@s.whatsapp.net"
AI = {"type": "agent_task", "receiver": REY, "receivers": [OWN]}
good = types.SimpleNamespace(content="  *LAPORAN HARIAN*\nOmzet naik")
bad = types.SimpleNamespace(content="Error: tool returned 500")


def run(channel, action, text):
    SENT.clear(), OWNER.clear()
    ch = wr.fanout(channel, action, "openai_api")
    ch.send(text)
    return ch


ch = run(wr.relay_for(REY), {"type": "agent_task", "receiver": REY}, good)
check("valid report -> recipient", [c for c, _ in SENT], ["628111@s.whatsapp.net"])
check("valid report, owner not warned", OWNER, [])
ch = run(None, AI, good)
check("valid report fan-out -> both", sorted(c for c, _ in SENT), ["628111@s.whatsapp.net", "64225802322@s.whatsapp.net"])
ch = run(None, AI, bad)
check("missing prefix fan-out -> nobody", SENT, [])
check("missing prefix -> owner only, with note",
      OWNER[0].startswith("⚠️ Not sent to Reyner: the result did not start with *LAPORAN.") and "500" in OWNER[0], True)
check("missing prefix noted in History", "Not delivered to Reyner" in ch.delivery_note and "owner only" in ch.delivery_note, True)
ch = run(wr.relay_for(REY), {"type": "agent_task", "receiver": REY}, bad)
check("missing prefix single -> owner only", (SENT, len(OWNER)), ([], 1))
ch = run(wr.relay_for(OWN), {"type": "agent_task", "receiver": OWN}, bad)
check("owner-only task unaffected", ([c for c, _ in SENT], OWNER, ch.delivery_note), (["64225802322@s.whatsapp.net"], [], ""))
ch = run(wr.relay_for(REY), {"type": "send_message", "receiver": REY}, types.SimpleNamespace(content="Ingat rapat jam 3"))
check("reminder unaffected", ([c for c, _ in SENT], OWNER), (["628111@s.whatsapp.net"], []))
ch = run(None, {"type": "send_message", "receiver": REY, "receivers": [OWN]}, types.SimpleNamespace(content="Ingat rapat"))
check("reminder fan-out unaffected", (len(SENT), OWNER), (2, []))
os.environ["LRMAGENT_REPORT_PREFIX"] = ""
ch = run(wr.relay_for(REY), {"type": "agent_task", "receiver": REY}, bad)
check("empty prefix disables the rule", (len(SENT), OWNER), (1, []))
del os.environ["LRMAGENT_REPORT_PREFIX"]
check("prefix case-insensitive", wr.report_guard(AI, [REY], "*Laporan Mingguan"), None)

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
