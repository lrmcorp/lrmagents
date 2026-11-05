"""ask_teammate: hand a task to a coding teammate (Codex, Claude Code, Gemini,
Grok or DeepSeek) running on the owner's own plans in the teammates container.

Only the owner may use them: from the console, or from the WhatsApp number's own
chat ("Message yourself"). Other WhatsApp contacts and API callers are refused,
because these are personal subscriptions. The tool waits a little for the answer;
longer tasks report back on WhatsApp (or in Config → AI plans) when done.

Installed into LRMagent by its image (deploy/agent-tools); see README.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger
from config import conf

TEAMMATES_URL = os.environ.get("TEAMMATES_URL", "http://teammates:8090").rstrip("/")
GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
WHATSAPP_PREFIX = "openai:conversation:whatsapp:"
MAX_WAIT = 240


def _call(url, body=None, timeout=20):
    token = str(conf().get("external_api_token") or "")
    request = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read() or b"{}").get("error")
        except ValueError:
            message = None
        raise RuntimeError(message or f"teammates service answered {e.code}")


class AskTeammate(BaseTool):
    name: str = "ask_teammate"
    description: str = (
        "Hand a task to a coding teammate that runs on the owner's own AI plans: 'codex' (OpenAI Codex, ChatGPT plan), "
        "'claude' (Claude Code, Claude plan), 'gemini' (Gemini CLI), 'grok' (xAI Grok) or 'deepseek'. Use it when the "
        "user asks for one by name (e.g. 'ask Codex to fix the bug in the ln repo', 'let Claude write the report'), or "
        "for a substantial coding job they want delegated. Each teammate works in its own folder on the server, can "
        "clone repositories, run code and tests, and remembers its previous session in that folder. Actions: 'list' "
        "(who is signed in), 'start' (give a task), 'status' (check a task by job_id), 'stop'. Write the task fully and "
        "self-contained: the teammate does not see this conversation."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "start", "status", "stop"]},
            "teammate": {"type": "string", "enum": ["codex", "claude", "gemini", "grok", "deepseek"]},
            "task": {"type": "string", "description": "For 'start': the complete task, with repository URLs, file names and what done looks like"},
            "folder": {"type": "string", "description": "Project folder name (letters, numbers, - _ .); the same name continues earlier work. Default 'main'"},
            "fresh": {"type": "boolean", "description": "Start a new session instead of continuing the previous one in that folder"},
            "job_id": {"type": "string", "description": "For 'status' and 'stop'"},
            "wait_seconds": {"type": "integer", "description": "How long to wait for the result before returning (default 120, max 240)"},
        },
        "required": ["action"],
    }

    # Who is asking

    def _session(self):
        model = getattr(self, "model", None)
        return str(getattr(model, "session_id", "") or ""), getattr(model, "agent_id", "") or ""

    def _owner(self, session_id):
        """(allowed, notify target or None, reason)."""
        if not session_id.startswith("openai:"):
            return True, None, ""  # the console: only the owner can sign in to it
        if not session_id.startswith(WHATSAPP_PREFIX):
            return False, None, "Teammates use the owner's personal plans, so they can't be used from the API."
        agent_id, _, chat = session_id[len(WHATSAPP_PREFIX):].partition(":")
        query = urllib.parse.urlencode({"agent": agent_id, "chat": chat})
        try:
            owner = _call(f"{GATEWAY_URL}/whatsapp/internal/owner?{query}").get("owner") is True
        except Exception as e:
            logger.warning(f"[ask_teammate] owner check failed: {e}")
            owner = False
        if not owner:
            return False, None, ("Teammates run on the owner's personal plans, so only the owner can use them: "
                                 "from the LRMagent console or the WhatsApp number's own chat (Message yourself).")
        return True, {"agentId": agent_id, "chat": chat}, ""

    # Actions

    def execute(self, args: dict) -> ToolResult:
        action = (args.get("action") or "").strip()
        session_id, _ = self._session()
        allowed, notify, reason = self._owner(session_id)
        if not allowed:
            return ToolResult.fail(reason)
        try:
            if action == "list":
                rows = _call(f"{TEAMMATES_URL}/teammates/internal/state").get("teammates", [])
                lines = [f"- {r['id']} ({r['name']}, {r['plan']}): " + ("signed in" if r.get("signedIn") else "NOT signed in")
                         + (f", model {r['model']}" if r.get("model") else "") for r in rows]
                return ToolResult.success("Teammates:\n" + "\n".join(lines)
                                          + "\nOne that is not signed in needs a one-time sign-in in the console under Config → AI plans.")
            if action == "start":
                return self._start(args, notify)
            job_id = (args.get("job_id") or "").strip()
            if not job_id.isalnum():
                return ToolResult.fail("Give the job_id of the task.")
            if action == "status":
                job = _call(f"{TEAMMATES_URL}/teammates/internal/jobs/{job_id}").get("job", {})
                return ToolResult.success(self._describe(job))
            if action == "stop":
                _call(f"{TEAMMATES_URL}/teammates/internal/jobs/{job_id}/stop", {})
                return ToolResult.success("Asked the teammate to stop.")
            return ToolResult.fail("action must be 'list', 'start', 'status' or 'stop'.")
        except Exception as e:
            return ToolResult.fail(f"Teammates: {e}")

    def _start(self, args, notify):
        teammate = (args.get("teammate") or "").strip()
        task = (args.get("task") or "").strip()
        if not teammate or not task:
            return ToolResult.fail("Say which teammate and what the task is.")
        try:
            wait = max(0, min(int(args.get("wait_seconds") or 120), MAX_WAIT))
        except (TypeError, ValueError):
            wait = 120
        job = _call(f"{TEAMMATES_URL}/teammates/internal/jobs", {
            "teammate": teammate, "task": task, "folder": (args.get("folder") or "main").strip() or "main",
            "fresh": bool(args.get("fresh")), "notify": notify, "wait": wait > 0,
        }).get("job", {})
        job_id = job.get("id", "")
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(3)
            job = _call(f"{TEAMMATES_URL}/teammates/internal/jobs/{job_id}").get("job", job)
            if job.get("status") not in ("queued", "running"):
                # Answered here, so the teammates service does not also message WhatsApp.
                try:
                    _call(f"{TEAMMATES_URL}/teammates/internal/jobs/{job_id}/ack", {})
                except Exception:
                    pass
                return ToolResult.success(self._describe(job))
        where = "I'll get the result on this WhatsApp chat when it's done." if notify else \
            "Follow it in Config → AI plans, or ask me for its status."
        return ToolResult.success(f"{teammate} is still working on it (job {job_id}). {where} "
                                  "Tell the user that, briefly; don't wait for it.")

    @staticmethod
    def _describe(job):
        status = job.get("status", "?")
        head = {"done": "Finished", "failed": "Could not finish", "stopped": "Stopped",
                "running": "Still working", "queued": "Waiting to start"}.get(status, status)
        text = f"{head} (job {job.get('id')}, {job.get('teammate')}, folder {job.get('folder')})."
        if job.get("error"):
            text += f"\nProblem: {job['error']}"
        if job.get("result"):
            text += f"\n\nTeammate's report:\n{job['result'][-6000:]}"
        return text
