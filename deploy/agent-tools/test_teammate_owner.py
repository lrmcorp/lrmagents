"""Stubbed tests for ask_teammate's owner check (python3 test_teammate_owner.py).

Teammates use the owner's personal plans, so the tool goes through lrm_owner.owner_session:
a task scheduled from a contact's chat is refused, the owner's voice page and agent-to-agent
reports are allowed. Runs standalone: container-only imports are stubbed and HTTP is faked.
"""

import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


class _Result(types.SimpleNamespace):
    @classmethod
    def success(cls, result):
        return cls(status="success", result=result)

    @classmethod
    def fail(cls, result):
        return cls(status="error", result=result)


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("config").conf = lambda: {"external_api_token": "t"}
_mod("agent")
_mod("agent.tools")
_mod("agent.tools.base_tool").BaseTool, sys.modules["agent.tools.base_tool"].ToolResult = object, _Result
_mod("agent.tools.scheduler")
_TASKS = {}
_mod("agent.tools.scheduler.integration").get_task_store = lambda: types.SimpleNamespace(get_task=lambda tid: _TASKS.get(tid))

sys.path[:0] = [HERE, os.path.join(HERE, "teammates")]
import lrm_owner  # noqa: E402
sys.modules["agent.lrm_owner"] = lrm_owner
import teammate  # noqa: E402

import urllib.request  # noqa: E402

OWNER_CHATS = {"628111@s.whatsapp.net"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(req, timeout=10):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "/whatsapp/internal/owner" in url:
        chat = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["chat"][0]
        return _Resp(json.dumps({"owner": chat in OWNER_CHATS}).encode())
    return _Resp(json.dumps({"teammates": []}).encode())


urllib.request.urlopen = _urlopen
import urllib.parse  # noqa: E402

WA = "openai:conversation:whatsapp:default:"
_TASKS["contact1"] = {"action": {"origin_session_id": WA + "62999@s.whatsapp.net"}}
_TASKS["owner1"] = {"action": {"origin_session_id": WA + "628111@s.whatsapp.net"}}
_TASKS["console1"] = {"action": {}}
PASS = {"n": 0, "f": 0}


def allowed(session_id):
    tool = teammate.AskTeammate()
    tool.model = types.SimpleNamespace(session_id=session_id, agent_id="default")
    return tool.execute({"action": "list"}).status == "success"


def check(name, got, want):
    PASS["n"] += 1
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


check("task scheduled from a contact's chat refused", allowed("scheduler_run1_contact1"), False)
check("unknown scheduled task refused", allowed("scheduler_run1_gone"), False)
check("contact's WhatsApp chat refused", allowed(WA + "62999@s.whatsapp.net"), False)
check("plain API caller refused", allowed("openai:conversation:someone"), False)
check("owner's voice page allowed", allowed("openai:conversation:lrm-voice:default:2026-11-23"), True)
check("agent-to-agent report allowed", allowed("openai:conversation:lrm-report:cfo:default:2026-11-23"), True)
check("owner's WhatsApp chat allowed", allowed(WA + "628111@s.whatsapp.net"), True)
check("task from owner's WhatsApp allowed", allowed("scheduler_run2_owner1"), True)
check("task made on the Tasks page allowed", allowed("scheduler_run3_console1"), True)
check("console allowed", allowed("web-session-1"), True)

tool = teammate.AskTeammate()
check("owner's WhatsApp target kept for notifications", tool._owner(WA + "628111@s.whatsapp.net")[1],
      {"agentId": "default", "chat": "628111@s.whatsapp.net"})

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
