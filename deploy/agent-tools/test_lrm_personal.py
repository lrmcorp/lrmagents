"""Stubbed tests for lrm_personal.refusal (python3 test_lrm_personal.py).

Other agents may not reach Sora's workspace (agents/personal) by a relative path, ~, a
search over a folder above it, or a shell glob/cd; each agent still reaches its own.
Runs standalone: a temporary home stands in for /home/agent, the registry is stubbed.
"""

import os
import sys
import tempfile
import types
from pathlib import Path


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)

HOME = os.path.realpath(tempfile.mkdtemp())
os.environ["HOME"] = HOME
COW = os.path.join(HOME, "cow")
PERSONAL = os.path.join(COW, "agents", "personal")
CFO = os.path.join(COW, "agents", "cfo")
for d in (os.path.join(PERSONAL, "memory"), os.path.join(CFO, "memory")):
    os.makedirs(d)
Path(PERSONAL, "MEMORY.md").write_text("private")
Path(CFO, "MEMORY.md").write_text("cfo")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_personal  # noqa: E402

lrm_personal.workspace = lambda: Path("~/cow/agents/personal")  # as the registry stores it
PASS = {"n": 0, "f": 0}


def executor(agent_id, cwd):
    return types.SimpleNamespace(model=types.SimpleNamespace(agent_id=agent_id),
                                 agent=types.SimpleNamespace(effective_cwd=lambda: cwd))


def check(name, got, want):
    PASS["n"] += 1
    if bool(got) != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {'refused' if want else 'allowed'}")
    else:
        print(f"ok   {name}")


default = executor("default", COW)  # the default agent works in ~/cow, above every agent's folder
cfo = executor("cfo", CFO)
R = lrm_personal.refusal

# Refused: every way to Sora's files.
check("read relative", R(default, "read", {"path": "agents/personal/MEMORY.md"}), True)
check("read ~", R(cfo, "read", {"path": "~/cow/agents/personal/MEMORY.md"}), True)
check("read absolute", R(cfo, "read", {"path": os.path.join(PERSONAL, "MEMORY.md")}), True)
check("read ../ escape", R(cfo, "read", {"path": "../personal/MEMORY.md"}), True)
check("search ancestor dir", R(cfo, "search_files", {"path": os.path.join(COW, "agents"), "pattern": "x"}), True)
check("search no path above her", R(default, "search_files", {"pattern": "x", "target": "content"}), True)
check("bash glob", R(cfo, "bash", {"command": f"cat {COW}/agents/pers*/MEMORY.md"}), True)
check("bash ~ glob", R(cfo, "bash", {"command": "cat ~/cow/agents/pers*/MEMORY.md"}), True)
check("bash cd + grep -r ?", R(cfo, "bash", {"command": f"cd {COW}/agents && grep -r . person?l/memory"}), True)
check("bash recursive walk above", R(cfo, "bash", {"command": f"grep -rn secret {COW}/agents"}), True)
check("bash relative from ~/cow", R(default, "bash", {"command": "cat agents/personal/MEMORY.md"}), True)

# Allowed: an agent's own workspace, and Sora herself.
check("own read relative", R(cfo, "read", {"path": "MEMORY.md"}), False)
check("own search no path", R(cfo, "search_files", {"pattern": "x", "target": "files"}), False)
check("own search memory", R(cfo, "search_files", {"path": "memory", "pattern": "x"}), False)
check("own bash grep -r", R(cfo, "bash", {"command": "grep -rn total ."}), False)
check("own bash cat", R(cfo, "bash", {"command": "cat memory/notes.md MEMORY.md"}), False)
check("sora reads her own", R(executor("personal", PERSONAL), "read", {"path": "MEMORY.md"}), False)
check("web url ignored", R(cfo, "read", {"path": "https://example.com/agents/personal"}), False)

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
