"""Standalone tests for the Personal-page voice helpers (python3 test_lrm_voice.py).

reflex() answers the current time/date without a model; split_hud() pulls the HUD block out
of an answer. The in-container modules the handler imports are stubbed so this runs anywhere.
"""
import os
import re
import sys
import types


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("web")
_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_common = _mod("channel")
_mod("channel.web")
_mod("channel.web.core")
_mod("channel.web.core._common")._require_auth = lambda *a, **k: None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_voice_api as _voice_src  # noqa: E402

PASS = {"n": 0, "f": 0}


def check(name, ok):
    PASS["n"] += 1
    if ok:
        print(f"ok   {name}")
    else:
        PASS["f"] += 1
        print(f"FAIL {name}")


def rx(name, text, lang, pattern):
    r = _voice_src.reflex(text, lang)
    check(name, r is not None and bool(re.match(pattern, r["text"])) and r["hud"] == [])


def none(name, text, lang=None):
    check(name, _voice_src.reflex(text, lang) is None)


# Time and date, English and Bahasa Indonesia.
rx("time en", "what time is it", None, r"^It's \d{2}:\d{2}\.$")
rx("time en short", "the time?", None, r"^It's \d{2}:\d{2}\.$")
rx("time id", "jam berapa", None, r"^Sekarang jam \d{2}:\d{2}\.$")
rx("time id sekarang", "jam berapa sekarang?", None, r"^Sekarang jam \d{2}:\d{2}\.$")
rx("time en forced", "jam berapa", "en", r"^It's \d{2}:\d{2}\.$")
rx("date en", "what's the date", None, r"^Today is \w+, \d{2}-\d{2}-\d{4}\.$")
rx("date en today", "what day is it today?", None, r"^Today is \w+, \d{2}-\d{2}-\d{4}\.$")
rx("date id", "hari apa ini", None, r"^Hari ini \w+, \d{2}-\d{2}-\d{4}\.$")
rx("date id tanggal", "tanggal berapa hari ini", None, r"^Hari ini \w+, \d{2}-\d{2}-\d{4}\.$")

# Not the current time/date: these must go to the model, not be hijacked.
none("meeting time en", "what time do we meet tomorrow")
none("meeting time id", "jam berapa meeting besok")
none("date in sentence", "can you move the date of the dentist")
none("empty", "")
none("greeting", "hey sora how are you")
none("long", "what time is it in Tokyo right now and also in London please")

# HUD split: the spoken text is clean, only known panel types survive.
sp, pn = _voice_src.split_hud('All good today.\n```hud\n[{"type":"stat","title":"Steps","value":"8,214"},'
                              '{"type":"bogus","x":1},{"type":"list","title":"Next","items":["A","B"]}]\n```')
check("hud spoken clean", sp == "All good today.")
check("hud panels filtered", [p["type"] for p in pn] == ["stat", "list"])
check("hud none", _voice_src.split_hud("Just talking, no panels.") == ("Just talking, no panels.", []))
check("hud cut by max_tokens not spoken", _voice_src.split_hud('All good.\n```hud\n[{"type":"stat","ti') == ("All good.", []))
check("other unclosed fence not spoken", _voice_src.split_hud("Here:\n```json\n{\"a\": 1") == ("Here:", []))
check("closed fence kept", _voice_src.split_hud("Run ```ls``` then go.")[0] == "Run ```ls``` then go.")
check("hud bad json ignored", _voice_src.split_hud("Hi.\n```hud\nnot json\n```")[0] == "Hi.")

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
