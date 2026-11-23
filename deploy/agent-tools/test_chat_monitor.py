"""Stubbed tests for chat_monitor's branch scope (python3 test_chat_monitor.py).

'digest' and 'report' are scoped like 'digest_all': in a scheduled run or an agent-to-agent
report a branch agent reads only its own branch; Lorium (the lead), Sora and Operations (for
the branches under it) keep their access, and the owner's own chats read every branch.
Runs standalone: container-only imports are stubbed and the gateway is faked.
"""

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
agent_pkg = _mod("agent")
_mod("agent.tools")
_mod("agent.tools.base_tool").BaseTool, sys.modules["agent.tools.base_tool"].ToolResult = object, _Result
_mod("agent.lrm_owner").owner_session = lambda session_id: (True, None)  # every caller here is the owner's
sys.modules["agent.lrm_owner"].REFUSAL = "owner only"
_mod("agent.lrm_personal").ID = "personal"
PROFILES = [types.SimpleNamespace(id=i, name=n) for i, n in (
    ("lorium", "Lorium"), ("operations", "Operations"), ("branch_a", "Branch A"), ("branch_b", "Branch B"),
    ("branch_c", "Branch C"), ("cfo", "CFO"), ("personal", "Sora"))]
_mod("agent.registry").get_agent_registry = lambda: types.SimpleNamespace(
    list=lambda include_disabled=False: PROFILES, default_agent_id="lorium")
ORG = {"lead": "lorium", "agents": [
    {"id": "operations", "reports_to": "lorium"}, {"id": "cfo", "reports_to": "lorium"},
    {"id": "branch_a", "reports_to": "operations"}, {"id": "branch_b", "reports_to": "operations"},
    {"id": "branch_c", "reports_to": "lorium"}]}
agent_pkg.lrm_org = types.SimpleNamespace(
    view=lambda: ORG, team_of=lambda me, v=None: [r for r in ORG["agents"] if r["reports_to"] == me])

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("chat_monitor_tool", os.path.join(HERE, "chat_monitor", "chat_monitor.py"))
chat_monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chat_monitor)

READ = []


def _get(path):
    if path.startswith("/whatsapp/internal/status"):
        return {"accounts": [{"agent": a, "mode": "monitor"} for a in ("branch_a", "branch_b", "branch_c")]}
    agent = path.split("agent=")[1].split("&")[0]
    READ.append(agent)
    return {"stats": {}, "chats": [], "timezone": "Asia/Jakarta"}


chat_monitor._get = _get
SCHEDULED, REPORT, CONSOLE = "scheduler_run1_task1", "openai:conversation:lrm-report:branch_a:lorium:2026-11-23", "web-1"
PASS = {"n": 0, "f": 0}


def call(me, session, action, agent=None):
    READ.clear()
    tool = chat_monitor.ChatMonitor()
    tool.model = types.SimpleNamespace(agent_id=me, session_id=session)
    out = tool.execute({"action": action, **({"agent": agent} if agent else {})})
    return out.status == "success", sorted(READ)


def check(name, got, want):
    PASS["n"] += 1
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


for session in (SCHEDULED, REPORT):
    tag = "scheduled" if session == SCHEDULED else "report"
    for action in ("digest", "report"):
        check(f"{tag}: branch reads own {action}", call("branch_a", session, action), (True, ["branch_a"]))
        check(f"{tag}: branch refused other branch's {action}", call("branch_a", session, action, "branch_b"), (False, []))
        check(f"{tag}: branch refused other by name", call("branch_a", session, action, "Branch C"), (False, []))
        check(f"{tag}: lorium reads any {action}", call("lorium", session, action, "branch_c"), (True, ["branch_c"]))
        check(f"{tag}: sora reads any {action}", call("personal", session, action, "branch_b"), (True, ["branch_b"]))
        check(f"{tag}: operations reads its branch", call("operations", session, action, "branch_b"), (True, ["branch_b"]))
    check(f"{tag}: branch digest_all only own", call("branch_a", session, "digest_all"), (True, ["branch_a"]))
    check(f"{tag}: lorium digest_all every branch", call("lorium", session, "digest_all"),
          (True, ["branch_a", "branch_b", "branch_c"]))
    check(f"{tag}: operations digest_all its branches", call("operations", session, "digest_all"),
          (True, ["branch_a", "branch_b"]))

# The owner's own chat with an agent reads every branch.
check("console: owner reads another branch", call("branch_a", CONSOLE, "report", "branch_b"), (True, ["branch_b"]))
check("console: operations digest of any branch", call("operations", CONSOLE, "digest", "branch_c"), (True, ["branch_c"]))

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
