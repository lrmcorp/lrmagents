"""Stubbed tests for approvals in lrm_activity (python3 test_lrm_activity.py).

An approval belongs to the whole call (tool + every argument, hashed), long text shows its
head and tail, recipients always show, and a click whose label pays, confirms, sends or
deletes asks even when the label also says "sign in". Runs standalone: container-only
imports are stubbed and the approvals database lives in a temporary folder.
"""

import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = tempfile.mkdtemp()


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("config").get_data_root = lambda: DATA
_mod("agent")
_mod("agent.lrm_sandbox").private_path_refusal = lambda *a: None
_mod("agent.lrm_personal").refusal = lambda *a: None
_mod("agent.lrm_vault_flow").refusal = lambda *a: None
sys.modules["agent"].lrm_task_runs = types.SimpleNamespace(scheduled_duplicate=lambda *a: None)

sys.path.insert(0, HERE)
import lrm_activity as A  # noqa: E402

A._notify_owner = lambda *a: None
A.TIMEOUT = 1
PASS = {"n": 0, "f": 0}


def check(name, ok, detail=""):
    PASS["n"] += 1
    if not ok:
        PASS["f"] += 1
        print(f"FAIL {name} {detail}")
    else:
        print(f"ok   {name}")


# The key covers every argument: two commands that differ only after 400 characters differ.
long_a = "echo " + "x" * 600 + " && rm -rf /srv/a"
long_b = "echo " + "x" * 600 + " && rm -rf /srv/b"
check("key differs past 400 chars", A.approval_key("bash", {"command": long_a}) != A.approval_key("bash", {"command": long_b}))
check("key differs by tool", A.approval_key("bash", {"command": "ls"}) != A.approval_key("write", {"command": "ls"}))
check("key stable for same call", A.approval_key("bash", {"a": 1, "b": 2}) == A.approval_key("bash", {"b": 2, "a": 1}))

# Long text: head … tail, and a risky part in the middle stays visible.
s = A.redact("H" * 300 + "M" * 300 + "T" * 300)
check("redact head and tail", s.startswith("H") and s.endswith("T") and " … " in s and len(s) <= 410, s[-20:])
cmd = "echo " + "a" * 500 + " ; rm -rf /data/important ; echo " + "b" * 500
s = A.summarize("bash", {"command": cmd})
check("bash summary keeps the risky part", "rm -rf /data/important" in s, s)

# Recipients always show, however long the body.
body = "word " * 400
s = A.summarize("google", {"action": "gmail_send", "body": body, "to": "a@x.com", "cc": "c@x.com", "bcc": "hidden@y.com"})
check("gmail to/cc/bcc shown", all(r in s for r in ("to: a@x.com", "cc: c@x.com", "bcc: hidden@y.com")), s[:120])
s = A.summarize("whatsapp", {"action": "send", "text": body, "to": "+62 812 0000"})
check("whatsapp recipient shown", "to: +62 812 0000" in s, s[:80])

# Sign-in labels: plain sign-in is free, one that also pays/confirms/sends/deletes asks.
risky = lambda label, action="click": A.risk("browser", {"action": action, "ref": 3}, "risky", {3: label})
check("plain sign in not gated", risky('button "Sign in"') == "")
check("log in not gated", risky('button "Log in"') == "")
for word in ("Confirm", "Subscribe", "Pay", "Buy", "Purchase", "Delete", "Remove", "Transfer", "Send"):
    check(f"sign in + {word} gated", risky(f'button "Sign in and {word}"') != "")
check("confirmation word gated", risky('button "Log in to confirmation page"') != "")
check("submit never exempt", risky('button "Sign in"', "submit") != "")

# A repeat of a refused call is refused again; a call differing only past 400 chars is asked anew.
ex = types.SimpleNamespace(model=types.SimpleNamespace(agent_id="cfo", session_id="web1", channel_type="web"),
                           _emit_event=lambda *a, **k: None, cancel_event=None)
first = A.gate(ex, "bash", {"command": long_a}, "t1")
check("unanswered request expires", first and "Nobody approved" in first, first)
with A._lock, A._connect() as conn:
    conn.execute("UPDATE approvals SET status='denied'")
again = A.gate(ex, "bash", {"command": long_a}, "t2")
check("same call refused again", again and "already refused" in again, again)
other = A.gate(ex, "bash", {"command": long_b}, "t3")
check("different tail is a new request", other and "Nobody approved" in other, other)

# Page scripts are refused outright: no approval is created and the owner is not asked.
with A._lock, A._connect() as conn:
    before = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
blocked = A.gate(ex, "browser", {"action": "evaluate", "script": "document.title"}, "t4")
with A._lock, A._connect() as conn:
    after = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
check("evaluate refused without asking", blocked and "turned off" in blocked and after == before, blocked)
os.environ["LRMAGENT_BROWSER_SCRIPTS"] = "ask"
asked = A.gate(ex, "browser", {"action": "evaluate", "script": "document.title"}, "t5")
check("evaluate asks again when switched to ask", asked and "turned off" not in asked, asked)
del os.environ["LRMAGENT_BROWSER_SCRIPTS"]

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
