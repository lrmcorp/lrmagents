"""Stubbed tests for ask_agent's report conversation (python3 test_ask_agent.py).

Each asker gets its own report conversation with the target (lrm-report:<target>:<asker>:<day>),
so Sora's and Lorium's questions never share a history, and the session it becomes still
counts as the owner's (lrm_owner.OWNER_PREFIXES). Runs standalone with stubs.
"""

import importlib.util
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
_mod("config").conf = lambda: {"external_api_token": "t", "web_port": 9899}
agent_pkg = _mod("agent")
_mod("agent.tools")
_mod("agent.tools.base_tool").BaseTool, sys.modules["agent.tools.base_tool"].ToolResult = object, _Result
_mod("agent.lrm_personal").ID, sys.modules["agent.lrm_personal"].NAME = "personal", "Sora"
PROFILES = [types.SimpleNamespace(id=i, name=n, description="") for i, n in
            (("lorium", "Lorium"), ("cfo", "CFO"), ("personal", "Sora"))]
_mod("agent.registry").get_agent_registry = lambda: types.SimpleNamespace(
    list=lambda include_disabled=False: PROFILES, default_agent_id="lorium")
agent_pkg.lrm_org = types.SimpleNamespace(view=lambda: {"agents": []}, can_ask=lambda me, t, v=None: (True, ""))

sys.path.insert(0, HERE)
import lrm_owner  # noqa: E402
sys.modules["agent.lrm_owner"] = lrm_owner

_spec = importlib.util.spec_from_file_location("ask_agent_tool", os.path.join(HERE, "ask_agent", "ask_agent.py"))
ask_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ask_agent)

import urllib.request  # noqa: E402

SENT = []


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(req, timeout=10):
    SENT.append(json.loads(req.data))
    return _Resp(json.dumps({"choices": [{"message": {"content": "figures"}}]}).encode())


urllib.request.urlopen = _urlopen
PASS = {"n": 0, "f": 0}


def check(name, got, want):
    PASS["n"] += 1
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def ask(me):
    tool = ask_agent.AskAgent()
    tool.model = types.SimpleNamespace(agent_id=me, session_id="web-1")
    result = tool.execute({"action": "ask", "agent": "cfo", "question": "Revenue today?"})
    return result.status, SENT[-1]["conversation_id"]


status, by_sora = ask("personal")
_, by_lorium = ask("lorium")
check("asked", status, "success")
check("conversation names target and asker", by_sora.split(":")[:3], ["lrm-report", "cfo", "personal"])
check("askers don't share a conversation", by_sora != by_lorium, True)
check("lorium's conversation", by_lorium.split(":")[:3], ["lrm-report", "cfo", "lorium"])
session = f"openai:conversation:{by_sora}"  # how the chat API names the session
check("report session is the owner's", session.startswith(lrm_owner.OWNER_PREFIXES), True)
check("owner_session allows it", lrm_owner.owner_session(session)[0], True)

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
