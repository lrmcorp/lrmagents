"""Standalone tests for team_memory (python3 test_team_memory.py): who reads whose notes.

The personal assistant reads every agent, the lead included, and the knowledge vault; others keep
their chart rules. In-container modules are stubbed.
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


class _Result:
    def __init__(self, ok, text):
        self.ok, self.text = ok, text


class _Base:
    pass


_mod("agent")
_mod("agent.tools")
_mod("agent.tools.base_tool").BaseTool = _Base
sys.modules["agent.tools.base_tool"].ToolResult = types.SimpleNamespace(
    success=lambda t: _Result(True, t), fail=lambda t: _Result(False, t))
_mod("agent.lrm_owner").owner_session = lambda sid: (sid == "owner", "")
sys.modules["agent.lrm_owner"].REFUSAL = "owner only"
_mod("agent.lrm_personal").ID = "personal"

root = Path(tempfile.mkdtemp())
shared = root / "knowledge"
(shared / "sop").mkdir(parents=True)
(shared / "sop" / "kasir.md").write_text("SOP kasir: tutup kas jam 21:00.")
spaces = {}
for aid in ("lorium", "dd-sby", "personal"):
    ws = root / aid
    (ws / "memory").mkdir(parents=True)
    spaces[aid] = ws
(spaces["lorium"] / "MEMORY.md").write_text("Lorium: omzet Surabaya naik 12%.")
(spaces["dd-sby"] / "memory" / "2026-11-23.md").write_text("Surabaya: stok susu habis.")
(spaces["personal"] / "MEMORY.md").write_text("private")
_mod("common")
_mod("common.state_dir").knowledge_dir = lambda base=None: str(shared)
sys.modules["common"].state_dir = sys.modules["common.state_dir"]

rows = [{"id": "lorium", "name": "Lorium", "private": False, "lead": True, "reports_to": ""},
        {"id": "dd-sby", "name": "DD Surabaya", "private": False, "lead": False, "reports_to": "lorium"},
        {"id": "personal", "name": "Sora", "private": True, "lead": False, "reports_to": ""}]
org = _mod("agent.lrm_org")
org.view = lambda: {"agents": rows}
org.below = lambda me: [r for r in rows if r["reports_to"] == me]
_mod("agent.registry").get_agent_registry = lambda: types.SimpleNamespace(
    default_agent_id="lorium", get=lambda i, require_enabled=False: types.SimpleNamespace(workspace=str(spaces[i])))

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "team_memory_src", os.path.join(os.path.dirname(os.path.abspath(__file__)), "team_memory", "team_memory.py"))
_src = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_src)
TeamMemory = _src.TeamMemory

FAILS = []


def run(me, sid="owner", **args):
    t = TeamMemory()
    t.model = types.SimpleNamespace(agent_id=me, session_id=sid)
    return t.execute(args)


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


listed = run("personal", action="list").text
check("Sora lists Lorium (the lead) and the others", "Lorium [id lorium]" in listed and "DD Surabaya" in listed)
check("Sora sees the shared vault once", listed.count("Shared vault") == 1 and "knowledge/sop/kasir.md" in listed)
check("Sora's own space is not in the list", "Sora [id personal]" not in listed)
check("Sora searches across memory", "stok susu" in run("personal", action="search", query="stok susu").text)
check("Sora searches the vault", "kasir" in run("personal", action="search", query="tutup kas").text)
check("Sora reads a vault note", run("personal", action="read", agent="vault", path="knowledge/sop/kasir.md").text.endswith("SOP kasir: tutup kas jam 21:00."))
check("no climbing out of the vault", not run("personal", action="read", agent="vault", path="knowledge/../lorium/MEMORY.md").ok)
check("Sora reads Lorium's MEMORY.md", "omzet Surabaya" in run("personal", action="read", agent="lorium", path="MEMORY.md").text)
check("outside the owner's chats it is refused", not run("personal", sid="wa-group", action="list").ok)
check("a Level 1 still reads only its team, no vault", "Shared vault" not in run("lorium", action="list").text
      and "DD Surabaya" in run("lorium", action="list").text)
print("FAILED" if FAILS else "ALL PASSED")
sys.exit(1 if FAILS else 0)
