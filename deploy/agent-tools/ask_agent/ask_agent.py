"""ask_agent: the personal assistant asks another agent for a report, for the owner.

Only the personal agent may use it, and only in the owner's own chats. The other agent
answers in a separate conversation (lrm-report:<agent>:<day>), so its regular chats are
not mixed up, and it sees only the question, not the personal conversation around it.
"""
import datetime
import json
import os
import urllib.error
import urllib.request

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger
from config import conf


class AskAgent(BaseTool):
    name: str = "ask_agent"
    description: str = (
        "Ask one of the owner's other agents (e.g. Lorium, the business agent) for a report, status or answer, "
        "when the owner asks for it ('get me Lorium's report on sales', 'what did the team do today'). Actions: "
        "'list' (which agents exist and what they do), 'ask' (agent + question). Write the question fully: the "
        "other agent does not see this conversation. Never include the owner's private details in it."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "ask"]},
            "agent": {"type": "string", "description": "For 'ask': the agent id or name from 'list'"},
            "question": {"type": "string", "description": "For 'ask': the complete question"},
        },
        "required": ["action"],
    }

    def execute(self, args: dict) -> ToolResult:
        from agent.lrm_owner import REFUSAL, owner_session
        from agent.lrm_personal import ID, NAME
        model = getattr(self, "model", None)
        if str(getattr(model, "agent_id", "") or "") != ID:
            return ToolResult.fail(f"Only {NAME}, the owner's personal assistant, asks other agents for reports.")
        if not owner_session(str(getattr(model, "session_id", "") or ""))[0]:
            return ToolResult.fail(REFUSAL)
        from agent.registry import get_agent_registry
        others = [p for p in get_agent_registry().list(include_disabled=False) if p.id != ID]
        action = (args.get("action") or "").strip()
        if action == "list":
            if not others:
                return ToolResult.success("There are no other agents yet.")
            return ToolResult.success("Agents:\n" + "\n".join(f"- {p.id}: {p.name}" + (f" ({p.description})" if getattr(p, "description", None) else "")
                                                             for p in others))
        if action != "ask":
            return ToolResult.fail("action must be 'list' or 'ask'.")
        wanted = str(args.get("agent") or "").strip().lower()
        question = str(args.get("question") or "").strip()
        target = next((p for p in others if p.id.lower() == wanted or (p.name or "").lower() == wanted), None)
        if target is None:
            return ToolResult.fail("No such agent. Use action 'list' first.")
        if not question:
            return ToolResult.fail("Say what to ask.")
        token = str(conf().get("external_api_token") or "")
        port = int(conf().get("web_port") or os.environ.get("WEB_PORT") or 9899)
        day = datetime.date.today().isoformat()
        body = {"agent_id": target.id, "conversation_id": f"lrm-report:{target.id}:{day}", "stream": False,
                "messages": [{"role": "user", "content": f"[{NAME}, the owner's personal assistant, asks on the owner's behalf]\n{question}"}]}
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", method="POST", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=600) as r:
                answer = str(json.loads(r.read())["choices"][0]["message"]["content"]).strip()
        except urllib.error.HTTPError as e:
            try:
                why = json.loads(e.read() or b"{}").get("error", {}).get("message")
            except ValueError:
                why = None
            return ToolResult.fail(f"{target.name} could not answer: {why or e.code}")
        except Exception as e:
            logger.warning(f"[ask_agent] {target.id}: {e}")
            return ToolResult.fail(f"{target.name} could not answer: {e}")
        return ToolResult.success(f"{target.name} answered:\n{answer}")
