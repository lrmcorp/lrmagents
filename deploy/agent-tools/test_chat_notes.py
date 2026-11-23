"""Stubbed tests for chat-note privacy (python3 test_chat_notes.py).

Chat notes (memory/chats/) are the owner's: a WhatsApp contact, an API caller or a task one
of them scheduled can't open, list or search them, with file tools or bash, while the owner
can. Summaries skip scheduled runs and don't call a contact "Owner". Runs standalone:
container-only imports are stubbed, the gateway's owner check is faked.
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.realpath(tempfile.mkdtemp())
os.environ["HOME"] = HOME
WS = os.path.join(HOME, "cow")
os.makedirs(os.path.join(WS, "memory", "chats", "2026-11"))
os.makedirs(os.path.join(WS, "knowledge"))
open(os.path.join(WS, "memory", "chats", "2026-11", "23-11-2026 Payroll.md"), "w").write("private")
open(os.path.join(WS, "memory", "notes.md"), "w").write("ok")


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("config").conf = lambda: {"external_api_token": "t"}
sys.modules["config"].get_data_root = lambda: os.path.join(HOME, "data")
os.makedirs(os.path.join(HOME, "data"))
agent_pkg = _mod("agent")
_mod("agent.tools")
_mod("agent.tools.scheduler")
_TASKS = {}
_mod("agent.tools.scheduler.integration").get_task_store = lambda: types.SimpleNamespace(get_task=lambda tid: _TASKS.get(tid))
_mod("agent.registry").get_agent_registry = lambda: types.SimpleNamespace(
    list=lambda include_disabled=False: [types.SimpleNamespace(id="default", workspace="~/cow")])

sys.path.insert(0, HERE)
for name in ("lrm_owner", "lrm_personal", "lrm_memory", "lrm_sandbox", "lrm_activity"):
    module = __import__(name)
    sys.modules[f"agent.{name}"] = module
    setattr(agent_pkg, name, module)
import lrm_memory  # noqa: E402
import lrm_sandbox  # noqa: E402
import lrm_activity  # noqa: E402

OWNER_CHATS = {"628111@s.whatsapp.net"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(req, timeout=10):
    chat = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)["chat"][0]
    return _Resp(json.dumps({"owner": chat in OWNER_CHATS}).encode())


urllib.request.urlopen = _urlopen
WA = "openai:conversation:whatsapp:default:"
CONTACT, OWNER = WA + "62999@s.whatsapp.net", WA + "628111@s.whatsapp.net"
_TASKS["fromcontact"] = {"action": {"origin_session_id": CONTACT}}
PASS = {"n": 0, "f": 0}


def ex(session):
    return types.SimpleNamespace(model=types.SimpleNamespace(session_id=session, agent_id="default"),
                                 agent=types.SimpleNamespace(effective_cwd=lambda: WS))


def refused(session, tool, args):
    return bool(lrm_sandbox.private_path_refusal(ex(session), tool, args))


def check(name, got, want):
    PASS["n"] += 1
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


# File tools from a contact's chat.
check("contact: ls memory/chats", refused(CONTACT, "ls", {"path": "memory/chats"}), True)
check("contact: ls memory/chats/", refused(CONTACT, "ls", {"path": "memory/chats/"}), True)
check("contact: read a note", refused(CONTACT, "read", {"path": "memory/chats/2026-11/23-11-2026 Payroll.md"}), True)
check("contact: search memory/", refused(CONTACT, "search_files", {"path": "memory", "pattern": "x"}), True)
check("contact: search whole workspace", refused(CONTACT, "search_files", {"pattern": "x", "target": "content"}), True)
check("contact: search knowledge/ ok", refused(CONTACT, "search_files", {"path": "knowledge", "pattern": "x"}), False)
check("contact: read memory/notes.md ok", refused(CONTACT, "read", {"path": "memory/notes.md"}), False)
check("contact's scheduled task: read a note", refused("scheduler_r1_fromcontact", "read", {"path": "memory/chats/x.md"}), True)

# Bash from a contact's chat (it runs in the sandbox, which mounts the workspace).
check("contact bash: ls memory/chats", refused(CONTACT, "bash", {"command": "ls memory/chats"}), True)
check("contact bash: grep -r memory", refused(CONTACT, "bash", {"command": "grep -rn salary memory"}), True)
check("contact bash: glob", refused(CONTACT, "bash", {"command": "cat memory/c*/*/*.md"}), True)
check("contact bash: recursive in workspace", refused(CONTACT, "bash", {"command": "grep -rn salary"}), True)
check("contact bash: find", refused(CONTACT, "bash", {"command": f"find {WS} -name '*.md'"}), True)
check("contact bash: cat knowledge ok", refused(CONTACT, "bash", {"command": "cat knowledge/a.md"}), False)
check("contact bash: ls memory ok", refused(CONTACT, "bash", {"command": "ls memory"}), False)

# The owner's own WhatsApp chat keeps access.
check("owner: search memory/", refused(OWNER, "search_files", {"path": "memory", "pattern": "x"}), False)
check("owner: read a note", refused(OWNER, "read", {"path": "memory/chats/2026-11/23-11-2026 Payroll.md"}), False)
check("owner bash: grep -r memory", refused(OWNER, "bash", {"command": "grep -rn salary memory"}), False)

# memory_get / memory_search paths (relative to memory/).
check("hidden_from chats/ for contact", lrm_memory.hidden_from(CONTACT, "chats/2026-11/a.md"), True)
check("hidden_from chats folder itself", lrm_memory.hidden_from(CONTACT, "memory/chats"), True)
check("not hidden for console", lrm_memory.hidden_from("web1", "chats/2026-11/a.md"), False)


# ls memory/: the chats/ folder is left out for a contact, kept for the owner.
def listing(session):
    result = {"status": "success", "result": {"output": "chats/\nnotes.md", "entry_count": 2}}
    lrm_activity.record(ex(session), "ls", {"path": "memory"}, result)
    return result["result"]


check("contact: ls memory/ hides chats/", listing(CONTACT), {"output": "notes.md", "entry_count": 1})
check("owner: ls memory/ keeps chats/", listing(OWNER)["output"], "chats/\nnotes.md")

# Summaries: scheduled runs skipped, a contact is not labelled as the owner.
db = os.path.join(HOME, "conv.db")
with sqlite3.connect(db) as c:
    c.execute("CREATE TABLE sessions (agent_id, session_id, channel_type, title, last_active)")
    c.execute("CREATE TABLE messages (agent_id, session_id, seq, role, content, created_at)")
    old = time.time() - 3 * 3600
    for sid in (CONTACT, "scheduler_r1_fromcontact", "web1"):
        c.execute("INSERT INTO sessions VALUES ('default', ?, 'web', '', ?)", (sid, old))
        c.execute("INSERT INTO messages VALUES ('default', ?, 1, 'user', 'How much is the invoice?', ?)", (sid, old))
store = types.SimpleNamespace(_db_path=db, _agent_id="default")
_mod("agent.memory")
_mod("agent.memory.conversation_store")._message_is_scheduled_marker = lambda content: False
sys.modules["agent.memory.conversation_store"]._message_plain_text = lambda content: content
quiet = [s[0] for s in lrm_memory._quiet_sessions(store, {}, "default")]
check("scheduled run not summarised", "scheduler_r1_fromcontact" in quiet, False)
check("chats still summarised", sorted(quiet), sorted([CONTACT, "web1"]))
body = lrm_memory._conversation(store, CONTACT)[0]
check("contact labelled Contact", body.startswith("Contact: ") and "Owner" not in body, True)
check("console labelled Owner", lrm_memory._conversation(store, "web1")[0].startswith("Owner: "), True)

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
