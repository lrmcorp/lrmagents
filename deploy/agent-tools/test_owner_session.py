"""Stubbed tests for owner_session (python3 test_owner_session.py).

A scheduled run of a task the owner created may reach the owner's agents (CFO/HRD/
Operations); a WhatsApp group or any non-owner chat may not. Runs standalone: the
in-container modules lrm_owner imports are stubbed, and the WhatsApp owner check
(a gateway HTTP call) is replaced with a controllable answer.
"""

import io
import json
import os
import sys
import types


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("config").conf = lambda: {"external_api_token": "t"}
_mod("agent")
_mod("agent.tools")
_mod("agent.tools.scheduler")

_TASKS = {}
_mod("agent.tools.scheduler.integration").get_task_store = lambda: types.SimpleNamespace(
    get_task=lambda tid: _TASKS.get(tid))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_owner  # noqa: E402

import urllib.request  # noqa: E402

_GATEWAY_OWNER = {"val": False}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


urllib.request.urlopen = lambda req, timeout=10: _Resp(json.dumps({"owner": _GATEWAY_OWNER["val"]}).encode())

WA = "openai:conversation:whatsapp:default:"
PASS = {"n": 0, "f": 0}


def check(name, got, want):
    PASS["n"] += 1
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


# (a) a scheduled task the owner made on the console (no origin) is the owner — in either
# session-id field order, so the fix is robust to run-id/task-id ordering.
_TASKS.clear()
_TASKS["c34dcd76"] = {"action": {}}
check("sched console, runid_taskid", lrm_owner.owner_session("scheduler_ab12cd34_c34dcd76")[0], True)
check("sched console, taskid_runid", lrm_owner.owner_session("scheduler_c34dcd76_ab12cd34")[0], True)

# (a2) a task the owner set up from their own WhatsApp chat: the origin is the owner → allowed.
_TASKS["t2"] = {"action": {"origin_session_id": WA + "628111@s.whatsapp.net"}}
_GATEWAY_OWNER["val"] = True
check("sched owner whatsapp origin", lrm_owner.owner_session("scheduler_run99_t2")[0], True)

# (b) a WhatsApp group chat is never the owner.
_GATEWAY_OWNER["val"] = False
check("group chat denied", lrm_owner.owner_session(WA + "120363402009619635@g.us")[0], False)

# (b2) a task scheduled from a group / non-owner chat stays denied even via the scheduler.
_TASKS["t3"] = {"action": {"origin_session_id": WA + "120363402009619635@g.us"}}
check("sched from group denied", lrm_owner.owner_session("scheduler_run99_t3")[0], False)

# (b3) an unknown scheduled task is not the owner.
check("sched unknown task denied", lrm_owner.owner_session("scheduler_run99_nope")[0], False)

# (c) a refused scheduled run is recorded against that run (evidence for lrm_task_output);
# an allowed one is not.
import lrm_task_runs  # noqa: E402
lrm_task_runs.start("scheduler_run7_t3", {"id": "t3"})
lrm_owner.owner_session("scheduler_run7_t3")
check("refusal recorded", lrm_task_runs.was_refused("scheduler_run7_t3"), True)
lrm_task_runs.finish("scheduler_run7_t3")
check("refusal readable after finish", lrm_task_runs.was_refused(), True)
_GATEWAY_OWNER["val"] = True
lrm_task_runs.start("scheduler_run8_t2", {"id": "t2"})
lrm_owner.owner_session("scheduler_run8_t2")
check("allowed run not marked", lrm_task_runs.was_refused("scheduler_run8_t2"), False)
lrm_task_runs.finish("scheduler_run8_t2")

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
