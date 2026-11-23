"""Stubbed tests for site_login's browser routing (python3 test_site_login.py).

site_login goes through lrm_browser_tabs like the browser tool: a scheduled run signs in on
its own tab and the owner's page is put back afterwards; the owner's chats use the main
page. While the owner drives the browser, the gate holds site_login like browser calls.
Runs standalone: the browser service is a fake that runs submitted calls inline.
"""

import importlib.util
import os
import sys
import tempfile
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


class _Page:
    def __init__(self, name):
        self.name, self.closed = name, False

    def is_closed(self):
        return self.closed


class _Service:
    def __init__(self):
        self._page = _Page("main")
        self._context = types.SimpleNamespace(new_page=lambda: _Page("tab"))

    def _submit(self, fn, *a, **k):
        return fn(*a, **k)


SERVICE = _Service()
SIGNED_ON = []
_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("config").get_data_root = lambda: tempfile.mkdtemp()
agent_pkg = _mod("agent")
_mod("agent.tools")
_mod("agent.tools.base_tool").BaseTool, sys.modules["agent.tools.base_tool"].ToolResult = object, _Result
_mod("agent.tools.browser")
_mod("agent.tools.browser.browser_tool").BrowserTool = types.SimpleNamespace(_shared_service=SERVICE)
_mod("agent.lrm_owner").owner_session = lambda s: (True, None)
sys.modules["agent.lrm_owner"].REFUSAL = "owner only"
agent_pkg.lrm_logins = types.SimpleNamespace(sign_in=lambda page, url: (SIGNED_ON.append(page.name) or True, "Signed in."))
_mod("agent.lrm_sandbox").private_path_refusal = lambda *a: None
_mod("agent.lrm_personal").refusal = lambda *a: None
_mod("agent.lrm_vault_flow").refusal = lambda *a: None
agent_pkg.lrm_task_runs = types.SimpleNamespace(scheduled_duplicate=lambda *a: None)

sys.path.insert(0, HERE)
import lrm_browser_tabs  # noqa: E402
import lrm_activity  # noqa: E402
agent_pkg.lrm_browser_tabs = lrm_browser_tabs

_spec = importlib.util.spec_from_file_location("site_login_tool", os.path.join(HERE, "site_login", "site_login.py"))
site_login = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_login)
PASS = {"n": 0, "f": 0}


def check(name, got, want):
    PASS["n"] += 1
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def sign_in(session):
    tool = site_login.SiteLogin()
    tool.model = types.SimpleNamespace(session_id=session, agent_id="cfo")
    return tool.execute({"action": "sign_in", "url": "https://bank.example"}).status


check("scheduled run signs in", sign_in("scheduler_run1_task1"), "success")
check("on its own tab", SIGNED_ON[-1], "tab")
check("owner's page put back", SERVICE._page.name, "main")
check("owner's chat signs in", sign_in("web-1"), "success")
check("on the main page", SIGNED_ON[-1], "main")

# The owner is driving: site_login waits like the browser tool, then gives up.
lrm_activity.TIMEOUT = 1
lrm_activity._notify_owner = lambda *a: None
lrm_activity.set_browser_control(True)
ex = types.SimpleNamespace(model=types.SimpleNamespace(agent_id="cfo", session_id="web-1", channel_type="web"),
                           _emit_event=lambda *a, **k: None, cancel_event=None)
held = lrm_activity.gate(ex, "site_login", {"action": "sign_in", "url": "https://bank.example"}, "t1")
check("held while the owner drives", bool(held and "still using the browser" in held), True)
lrm_activity.set_browser_control(False)
check("runs once handed back", lrm_activity.gate(ex, "site_login", {"action": "sign_in", "url": "https://bank.example"}, "t2"), None)

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
