"""Stubbed tests for scheduled_duplicate (python3 test_lrm_task_runs.py): during a scheduled
run only whatsapp sends to the task's own recipients are refused."""

import os
import sys
import types

sys.modules["common"] = types.ModuleType("common")
_log = types.ModuleType("common.log")
_log.logger = types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None)
sys.modules["common.log"] = _log
_relay = types.ModuleType("channel.lrm_whatsapp_relay")
P = "openai:conversation:whatsapp:ops:"
_relay.task_recipients = lambda: [
    {"receiver": P + "628111222333@s.whatsapp.net", "name": "You (owner)"},
    {"receiver": P + "120363000000@g.us", "name": "Group: Kepala Cabang"},
    {"receiver": P + "628555666777@s.whatsapp.net", "name": "Hansel"},
]
sys.modules["channel"] = types.ModuleType("channel")
sys.modules["channel.lrm_whatsapp_relay"] = _relay

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_task_runs as runs  # noqa: E402

PASS = {"f": 0}


def check(name, got, want):
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


SID = "scheduler_r1_t1"
runs.start(SID, {"id": "t1", "name": "Laporan cabang", "action": {
    "receiver": P + "628111222333@s.whatsapp.net",
    "receivers": [P + "120363000000@g.us", P + "628555666777@s.whatsapp.net"]}})


def blocked(to):
    return runs.scheduled_duplicate(SID, "whatsapp", {"action": "send", "to": to}) is not None


check("owner blocked", blocked("me"), True)
check("owner number blocked", blocked("+62 811-1222-333"), True)
check("task group blocked", blocked("Group: Kepala Cabang"), True)
check("task contact blocked", blocked("Hansel"), True)
check("other group allowed (HR group)", blocked("Group: HRD"), False)
check("other number allowed", blocked("6289999888777"), False)
check("task contact by fuller name blocked", blocked("Pak Hansel"), True)
check("other contact sharing letters allowed", blocked("Hans"), False)
check("other group by jid allowed", blocked("120363999999@g.us"), False)
check("task group by jid blocked", blocked("120363000000@g.us"), True)
check("other tool allowed", runs.scheduled_duplicate(SID, "bash", {"action": "send", "to": "me"}), None)
check("non-send allowed", runs.scheduled_duplicate(SID, "whatsapp", {"action": "list"}), None)
runs.finish(SID)
check("no run, nothing blocked", runs.scheduled_duplicate(SID, "whatsapp", {"action": "send", "to": "me"}), None)

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
