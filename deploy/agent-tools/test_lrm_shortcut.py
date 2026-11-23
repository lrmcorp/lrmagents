"""Stubbed tests for the iPhone Shortcuts API (python3 test_lrm_shortcut.py).

Token auth and its throttle, the brief taken from the run history, and the payloads ask/tts
hand to Sora's chat and the voice service. The in-container modules are stubbed (web.py
request state, the voice handler module, the conversation store) so this runs anywhere.
"""
import json
import datetime
import re
import time
import os
import sys
import types


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_web = _mod("web")
_web.ctx = types.SimpleNamespace(env={}, status="200 OK")
_HEADERS = {}
_BODY = {"data": b""}
_web.header = lambda k, v: _HEADERS.__setitem__(k, v)
_web.data = lambda: _BODY["data"]

_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("channel")
_mod("channel.web")
_mod("channel.web.core")
_mod("channel.web.core._common")._client_ip = lambda: _web.ctx.env.get("REMOTE_ADDR", "unknown")
_api = _mod("channel.web.api")
_voice = _mod("channel.web.api.lrm_voice")
_api.lrm_voice = _voice
CALLS = []
_voice.chat = lambda text, image=None, lang=None, spoken_only=False: CALLS.append(("chat", text, image, lang, spoken_only)) or {"text": "Siap.", "hud": [], "agent": {}}


def _fake_voice(path, data=None, headers=None, timeout=180):
    CALLS.append(("voice", path, json.loads(data), headers))
    return b"ID3mp3", {"Content-Type": "audio/mpeg"}


_voice._voice = _fake_voice


class _Store:
    def __init__(self, rows, full=None):
        self.rows, self.full = rows, full or {}

    def list_runs(self, task_source=None, limit=100, offset=0, **kw):
        return [r for r in self.rows if r.get("task_source", "scheduler") == task_source][offset:offset + limit]

    def get_run_detail(self, run_id):
        return {"full_output": self.full.get(run_id)}


_STORE = {"store": _Store([])}
_mod("agent")
_mod("agent.memory").get_conversation_store = lambda: _STORE["store"]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_shortcut_api as api  # noqa: E402

PASS = {"n": 0, "f": 0}
TOKEN = "s" * 32


def check(name, got, want):
    PASS["n"] += 1
    if got == want:
        print(f"ok   {name}")
    else:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")


def call(method, op, token=TOKEN, body=None, ip="203.0.113.5"):
    _web.ctx.env = {"QUERY_STRING": f"op={op}", "REMOTE_ADDR": ip}
    if token is not None:
        _web.ctx.env["HTTP_X_LRM_TOKEN"] = token
    _web.ctx.status = "200 OK"
    _HEADERS.clear()
    _BODY["data"] = json.dumps(body).encode() if body is not None else b""
    out = getattr(api.LrmShortcutHandler(), method)()
    status = int(_web.ctx.status.split()[0])
    if _HEADERS.get("Content-Type", "").startswith("application/json"):
        out = json.loads(out)
    return status, out


# Off unless a long enough token is configured.
os.environ.pop("LRMAGENT_SHORTCUT_TOKEN", None)
check("no env -> 404", call("GET", "brief")[0], 404)
os.environ["LRMAGENT_SHORTCUT_TOKEN"] = "short-token-123"
check("short env -> 404", call("GET", "brief", token="short-token-123")[0], 404)
os.environ["LRMAGENT_SHORTCUT_TOKEN"] = TOKEN

check("right token", call("GET", "brief")[0], 200)
check("wrong token -> 401", call("GET", "brief", token="x" * 32, ip="198.51.100.1"), (401, {"error": "wrong token"}))
check("missing token -> 401", call("GET", "brief", token=None, ip="198.51.100.2")[0], 401)

# Throttle: the 11th try from one client within 10 minutes is refused, even with the right token.
api._failures.clear()
codes = [call("GET", "brief", token="bad" * 10, ip="192.0.2.9")[0] for _ in range(10)]
check("10 failures all 401", set(codes), {401})
check("then 429", call("GET", "brief", token="bad" * 10, ip="192.0.2.9")[0], 429)
check("429 with the right token too", call("GET", "brief", ip="192.0.2.9")[0], 429)
check("retry-after set", "Retry-After" in _HEADERS, True)
check("other client unaffected", call("GET", "brief", ip="192.0.2.10")[0], 200)
api._failures["192.0.2.9"] = [t - api.WINDOW for t in api._failures["192.0.2.9"]]
check("window over -> allowed", call("GET", "brief", ip="192.0.2.9")[0], 200)

# Brief: none yet.
_STORE["store"] = _Store([])
check("no brief", call("GET", "brief")[1], {"text": "Belum ada ringkasan operasional.", "at": "", "task": ""})

# 2026-11-23 01:00 UTC is 08:00 in Jakarta.
T = 1795395600
rows = [
    {"run_id": "r5", "status": "running", "started_at": T + 500, "extras": {"task_name": "Ringkasan Operasional Pagi"}},
    {"run_id": "r4", "status": "error", "started_at": T + 400, "ended_at": T + 410,
     "extras": {"task_name": "Ringkasan Operasional Pagi", "output_preview": "failed one"}},
    {"run_id": "r3", "status": "done", "started_at": T + 300, "ended_at": T + 310,
     "extras": {"task_name": "Laporan Kas", "output_preview": "other task"}},
    {"run_id": "r2", "status": "done", "started_at": T - 60, "ended_at": T,
     "extras": {"task_name": "ringkasan operasional harian", "output_preview":
                "[KIRIM]*LAPORAN Ringkasan* hari ini:\n- Penjualan _naik_ 5%\n- Stok aman"
                "\n\nDelivered: Reyner ✓, Hansel ✗ (offline)"}},
    {"run_id": "r1", "status": "done", "started_at": T - 9000, "ended_at": T - 8000,
     "extras": {"task_name": "Ringkasan Operasional", "output_preview": "older"}},
]
_STORE["store"] = _Store(rows)
status, brief = call("GET", "brief")
check("brief picks newest successful match", brief["task"], "ringkasan operasional harian")
check("brief text stripped", brief["text"], "LAPORAN Ringkasan hari ini:\n- Penjualan naik 5%\n- Stok aman")
check("brief time in WIB", brief["at"], "23-11-2026 08:00")

# The full delivered text wins over the clipped preview.
_STORE["store"] = _Store(rows, full={"r2": "Lengkap: *semua* cabang buka. [tagged +62811; msg ABC]"})
check("full output used", call("GET", "brief")[1]["text"], "Lengkap: semua cabang buka.")

# A silent run (nothing sent) is skipped.
_STORE["store"] = _Store([{"run_id": "s", "status": "done", "started_at": T, "ended_at": T,
                           "extras": {"task_name": "Ringkasan Operasional", "output_preview": ""}}] + rows[3:])
check("empty run skipped", call("GET", "brief")[1]["text"].startswith("LAPORAN"), True)

check("clean keeps snake_case and bullets", api.clean_brief("* item satu\nfile_name_x"), "* item satu\nfile_name_x")
check("clean not-delivered note", api.clean_brief("Isi\n\nNot delivered: the WhatsApp gateway refused it (x)"), "Isi")

# Ask: the same chat the Personal page uses, owner's text and language.
CALLS.clear()
check("ask answer", call("POST", "ask", body={"text": " Halo Sora ", "lang": "id"}), (200, {"text": "Siap."}))
check("ask payload", CALLS, [("chat", "Halo Sora", None, "id", True)])
check("ask without text", call("POST", "ask", body={"text": "  "})[0], 400)
check("ask too long", call("POST", "ask", body={"text": "a" * 2001})[0], 413)
check("ask not JSON", call("POST", "ask", body=None)[0], 400)
_voice.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the personal assistant does not exist yet"))
check("ask failure is JSON", call("POST", "ask", body={"text": "hi"}),
      (503, {"error": "the personal assistant does not exist yet"}))

# TTS: Google engine through the voice service, WAV back.
CALLS.clear()
status, audio = call("POST", "tts", body={"text": "Selamat pagi"})
check("tts audio", (status, audio, _HEADERS.get("Content-Type")), (200, b"ID3mp3", "audio/mpeg"))
check("tts payload", CALLS, [("voice", "/tts", {"text": "Selamat pagi", "format": "mp3", "engine": "google"},
                              {"Content-Type": "application/json"})])
check("tts too long", call("POST", "tts", body={"text": "a" * 1501})[0], 413)

CALLS.clear()
_STORE["store"] = _Store(rows)
status, audio = call("POST", "brief_audio")
check("brief_audio", (status, audio), (200, b"ID3mp3"))
check("brief_audio greets, then reads the brief", re.match(r"^Halo, berikut laporan operasional (?:terakhir, \d{2}-\d{2}-\d{4} pukul|\w+ ini, pukul) \d{2}:\d{2}\. LAPORAN Ringkasan", CALLS[0][2]["text"]) is not None, True)
CALLS.clear()
call("POST", "brief_audio", body={"name": "Budi"})
check("brief_audio with the owner's name", CALLS[0][2]["text"].startswith("Halo Budi, berikut laporan operasional "), True)
at = datetime.datetime.strptime(api.latest_brief()["at"], "%d-%m-%Y %H:%M")
check("brief today: part of day", api._brief_speech("Budi", now=at).split(". ")[0],
      f"Halo Budi, berikut laporan operasional {'pagi' if at.hour < 11 else 'siang' if at.hour < 15 else 'sore' if at.hour < 18 else 'malam'} ini, pukul {at:%H:%M}")
check("brief from an earlier day says its date", api._brief_speech("", now=at + datetime.timedelta(days=1)).split(". ")[0],
      f"Halo, berikut laporan operasional terakhir, {at:%d-%m-%Y} pukul {at:%H:%M}")
check("owner name is cleaned", api._owner_name({"name": " Budi<script> "}), "Budiscript")

# ask_audio: one request asks Sora and returns her voice, in the language the owner spoke.
_voice.chat = lambda text, image=None, lang=None, spoken_only=False: CALLS.append(("chat", text, image, lang, spoken_only)) or {"text": "Jam tiga sore.", "hud": [], "agent": {}}
CALLS.clear()
status, audio = call("POST", "ask_audio", body={"text": "jam berapa?", "lang": "id"})
check("ask_audio audio", (status, audio, _HEADERS.get("Content-Type")), (200, b"ID3mp3", "audio/mpeg"))
check("ask_audio calls", CALLS, [("chat", "jam berapa?", None, "id", True),
                                 ("voice", "/tts", {"text": "Jam tiga sore.", "format": "mp3", "engine": "google", "lang": "id"},
                                  {"Content-Type": "application/json"})])
# A report request reads the saved brief without the model.
CALLS.clear()
_STORE["store"] = _Store(rows)
for ask in ("laporan harian", "Tolong bacakan ringkasan operasional hari ini", "Laporan", "daily report please"):
    CALLS.clear()
    call("POST", "ask_audio", body={"text": ask})
    check(f"brief ask: {ask}", ([c[0] for c in CALLS], CALLS[-1][2]["text"].startswith("Halo, berikut laporan operasional ")), (["voice"], True))
CALLS.clear()
call("POST", "ask_audio", body={"text": "buatkan laporan stok cabang Bekasi minggu ini"})
check("other report asks go to the model", CALLS[0][0], "chat")

# A slow model: the iPhone hears the note at once, the answer follows on WhatsApp.
import threading as _t
SENT, gate = [], _t.Event()
api._whatsapp_owner = lambda agent, text: SENT.append((agent, text)) or gate.set()
_voice.chat = lambda text, image=None, lang=None, spoken_only=False: time.sleep(0.3) or {"text": "Ada dua meeting.", "agent": {"id": "personal"}}
api.WAIT = 0.05
CALLS.clear()
status, _ = call("POST", "ask_audio", body={"text": "ada meeting apa besok?"})
check("slow answer: note spoken", (status, CALLS[-1][2]["text"]), (200, api.LATE))
check("slow answer: sent on WhatsApp", (gate.wait(3), SENT), (True, [("personal", "🎙 From your iPhone: ada meeting apa besok?\n\nAda dua meeting.")]))
api.WAIT = 25

check("ask_audio without text", call("POST", "ask_audio", body={"text": ""})[0], 400)
check("GET ask_audio -> 405", call("GET", "ask_audio")[0], 405)

check("unknown op", call("POST", "nope")[0], 404)
check("GET ask -> 405", call("GET", "ask")[0], 405)

print(f"\n{PASS['n'] - PASS['f']}/{PASS['n']} passed")
sys.exit(1 if PASS["f"] else 0)
