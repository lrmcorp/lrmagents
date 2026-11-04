"""Where an agent's command runs, and which files a chat may open.

Console chats are the owner's and run as before. WhatsApp and API chats
(``openai:`` sessions) run their shell commands in the sandbox container
(agent/lrm_sandbox_server.py), which has the workspace but none of LRMagent's
secrets, and may not open LRMagent's own settings with the file tools.
Hooked into the bash tool and the tool gate by the LRMagent image.
Installed as agent/lrm_sandbox.py.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from types import SimpleNamespace

SANDBOX_URL = os.environ.get("LRMAGENT_SANDBOX_URL", "").rstrip("/")
SANDBOX_TOKEN = os.environ.get("SANDBOX_TOKEN", "")
FILE_TOOLS = ("read", "ls", "search_files", "write", "edit", "send", "vision", "transcribe_audio")
_SECRET_NAMES = re.compile(r"(^|/)(config\.json|\.env(\..*)?|lrm_google\.json|.*\.sqlite|id_[a-z0-9]+|.*\.pem|.*\.key)$", re.I)


def sandboxed(tool_or_executor):
    session = str(getattr(getattr(tool_or_executor, "model", None), "session_id", "") or "")
    return session.startswith("openai:")


def run(tool, command, timeout, env, dotenv_vars):
    """The command's result from the sandbox for WhatsApp/API chats; None to run it here."""
    if not sandboxed(tool):
        return None
    if not (SANDBOX_URL and SANDBOX_TOKEN):
        return SimpleNamespace(returncode=126, stdout="", stderr=(
            "Commands from WhatsApp and API chats run in LRMagent's sandbox, which is not set up on this server."))
    # Only what a command may need: the skills' own keys (env_config) and the run identity.
    passed = dict(dotenv_vars or {})
    for key in ("AGENT_WORKSPACE", "COW_AGENT_RUN_ID", "COW_CLIENT_SOURCE"):
        if env.get(key):
            passed[key] = env[key]
    body = {"command": command, "cwd": getattr(tool, "cwd", "") or "", "timeout": int(timeout), "env": passed}
    request = urllib.request.Request(f"{SANDBOX_URL}/exec", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {SANDBOX_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=int(timeout) + 30) as r:
            out = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        return SimpleNamespace(returncode=126, stdout="", stderr=f"LRMagent's command sandbox did not answer ({e}). Try again in a moment.")
    if out.get("timed_out"):
        raise subprocess.TimeoutExpired(command, timeout)
    return SimpleNamespace(returncode=int(out.get("returncode", 1)), stdout=out.get("stdout", ""), stderr=out.get("stderr", ""))


def _private_roots():
    roots = []
    try:
        from config import get_data_root
        roots.append(os.path.realpath(get_data_root()))
    except Exception:
        pass
    roots += [os.path.realpath(os.path.expanduser("~/.cow")), "/proc", "/etc/shadow", "/run/secrets", "/root"]
    return [r for r in roots if r and r != "/"]


def private_path_refusal(executor, tool_name, arguments):
    """A refusal when a WhatsApp/API chat's file tool points at LRMagent's own settings, else None."""
    if tool_name not in FILE_TOOLS or not sandboxed(executor):
        return None
    workspace = ""
    try:
        workspace = os.path.realpath(executor.agent.effective_cwd())
    except Exception:
        pass
    values = []
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and key in ("path", "file_path", "file", "directory", "dir", "target", "image", "audio", "url"):
            values.append(value)
        elif isinstance(value, list) and key in ("paths", "files", "images"):
            values += [v for v in value if isinstance(v, str)]
    roots = _private_roots()
    for raw in values:
        if raw.startswith(("http://", "https://")):
            continue
        path = raw[7:] if raw.startswith("file://") else raw
        full = os.path.realpath(os.path.join(workspace or "/", os.path.expanduser(path)))
        from agent.lrm_memory import hidden_from
        if hidden_from(getattr(getattr(executor, "model", None), "session_id", ""), full):
            return "Chat notes are private to the owner, so they can't be opened from this chat."
        inside_private = any(full == r or full.startswith(r + os.sep) for r in roots)
        in_workspace = bool(workspace) and (full == workspace or full.startswith(workspace + os.sep))
        # Secret-looking files anywhere outside the workspace; a project's own config.json is fine.
        if inside_private or (not in_workspace and _SECRET_NAMES.search(full)):
            return ("That file belongs to LRMagent's private settings, so it can't be opened from WhatsApp or API chats. "
                    "Ask the owner to look at it in the console if it is needed.")
    return None
