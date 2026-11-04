"""whatsapp: the agent's own WhatsApp number, from any owner chat.

Shows whether a number is linked and lets the owner have messages sent: to
themselves ("me": their own numbers, or the linked phone's "Message yourself"),
or to a number that wrote to the agent or is approved. The gateway refuses
anyone else, so a misled agent can't message strangers. Owner only.
"""
import json
import os
import re
import urllib.error
import urllib.request

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger
from config import conf

GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")


def _call(path, body=None, timeout=30):
    token = str(conf().get("external_api_token") or "")
    request = urllib.request.Request(
        f"{GATEWAY_URL}{path}", data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read() or b"{}").get("error")
        except ValueError:
            message = None
        raise RuntimeError(message or f"WhatsApp gateway answered {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"the WhatsApp gateway is not reachable ({e.reason})")


class WhatsAppTool(BaseTool):
    name: str = "whatsapp"
    description: str = (
        "The agent's own WhatsApp number (linked on the console's WhatsApp page). Actions: 'status' (is a number linked, "
        "which one, the owner's numbers), 'send' (send a WhatsApp message now). For 'send', 'to' is 'me' for the owner "
        "(e.g. 'send me a WhatsApp', 'text me the summary') or a phone number with country code; only numbers that "
        "already wrote to the agent or are approved can be messaged. Use this whenever the owner asks to send or receive "
        "something on WhatsApp; for a message at a later time use the scheduler instead."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "send"]},
            "to": {"type": "string", "description": "For 'send': 'me', or a phone number with country code like 6281234567890"},
            "text": {"type": "string", "description": "For 'send': the message"},
        },
        "required": ["action"],
    }

    def _session(self):
        model = getattr(self, "model", None)
        return str(getattr(model, "session_id", "") or ""), str(getattr(model, "agent_id", "") or "")

    def execute(self, args: dict) -> ToolResult:
        from agent.lrm_owner import REFUSAL, owner_session
        session_id, agent_id = self._session()
        if not owner_session(session_id)[0]:
            return ToolResult.fail(REFUSAL)
        action = (args.get("action") or "").strip()
        try:
            state = _call("/whatsapp/internal/status")
            accounts = state.get("accounts") or []
            mine = next((a for a in accounts if a.get("agent") == (agent_id or "default")), None)
            linked = [a for a in accounts if a.get("status") == "connected"]
            if action == "status":
                if not accounts:
                    return ToolResult.success("No WhatsApp number is linked yet. Link one on the console's WhatsApp page.")
                lines = []
                for a in accounts:
                    lines.append(f"- agent '{a.get('agent')}': {a.get('status')}, number +{a.get('phone') or '?'}"
                                 + (f", owner numbers: {', '.join('+' + n for n in a.get('ownerNumbers') or [])}" if a.get("ownerNumbers") else
                                    ", no 'Your own numbers' set (owner messages go to the linked phone's Message yourself)"))
                return ToolResult.success("WhatsApp:\n" + "\n".join(lines))
            if action != "send":
                return ToolResult.fail("action must be 'status' or 'send'.")
            text = str(args.get("text") or "").strip()
            to = str(args.get("to") or "me").strip()
            if not text:
                return ToolResult.fail("Say what the message should say.")
            account = mine if mine and mine.get("status") == "connected" else (linked[0] if linked else None)
            if not account:
                return ToolResult.fail("No WhatsApp number is connected. Link one on the console's WhatsApp page.")
            if to.lower() in ("me", "owner", "myself", "self"):
                _call("/whatsapp/internal/notify-owner", {"agentId": account["agent"], "text": text})
                where = ", ".join("+" + n for n in account.get("ownerNumbers") or []) or f"+{account.get('phone')} (Message yourself)"
                return ToolResult.success(f"Sent to the owner on WhatsApp ({where.split(',')[0]}).")
            number = re.sub(r"\D", "", to)
            if len(number) < 6:
                return ToolResult.fail("Give the phone number with country code, e.g. 6281234567890, or 'me'.")
            _call("/whatsapp/internal/send", {"agentId": account["agent"], "chat": f"{number}@s.whatsapp.net", "text": text})
            return ToolResult.success(f"Sent to +{number} on WhatsApp.")
        except Exception as e:
            logger.warning(f"[whatsapp] {action} failed: {e}")
            return ToolResult.fail(f"WhatsApp: {e}")
