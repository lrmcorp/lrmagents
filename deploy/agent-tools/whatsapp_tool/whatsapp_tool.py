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
        "(e.g. 'send me a WhatsApp', 'text me the summary'), the name of a WhatsApp group the owner opened to agents, or the "
        "number of a designated contact (see 'status' for both): agents may "
        "contact anybody else first. A designated contact set to 'listen only' never gets an answer from you when they write "
        "back: Lorium tells the owner and you are told what they said, as information. Use this whenever the owner asks to send or receive "
        "something on WhatsApp; for a message at a later time use the scheduler instead. Write WhatsApp messages and reports as "
        "a short bold title (*Title*) and bullet points with the facts: no greeting, no filler, no closing line."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "send"]},
            "to": {"type": "string", "description": "For 'send': 'me', a group's name (from 'status'), or a phone number with country code like 6281234567890"},
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
                people = state.get("designated") or []
                lines.append("- designated contacts (the only people besides the owner agents may message): "
                             + (", ".join(f"{c.get('name') or 'unnamed'} +{c.get('phone')} ({'agent may reply' if c.get('mode') == 'reply' else 'listen only'})" for c in people) if people else
                                "none; the owner adds them on the console's WhatsApp page"))
                groups = state.get("groups") or []
                lines.append("- groups agents may post to: " + (", ".join(f"'{g.get('name')}'" for g in groups) if groups else
                                                                 "none; the owner ticks them under Groups on the console's WhatsApp page"))
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
                owners = account.get("ownerNumbers") or []
                if owners:
                    return ToolResult.success(f"Sent to the owner on WhatsApp at +{owners[0]}.")
                return ToolResult.success(
                    f"Sent to the 'Message yourself' chat of the linked phone (+{account.get('phone')}), because no personal "
                    "number is set. Tell the owner exactly that: it is on the linked phone, without a notification. To get "
                    "messages on their own phone they add its number under 'Your own numbers' on the console's WhatsApp page "
                    "(or you can send to a number directly with 'to').")
            # A group the owner opened to agents, by name (or its id).
            groups = state.get("groups") or []
            wanted = to.strip().lower().removeprefix("group").strip(" :")
            group = next((g for g in groups if wanted and (g.get("jid") == to.strip() or str(g.get("name", "")).lower() == wanted)), None) \
                or next((g for g in groups if wanted and len(wanted) >= 3 and wanted in str(g.get("name", "")).lower()), None)
            if group:
                via = next((a for a in accounts if a.get("agent") == group.get("agent") and a.get("status") == "connected"), account)
                _call("/whatsapp/internal/send", {"agentId": via["agent"], "chat": group["jid"], "text": text})
                return ToolResult.success(f"Posted in the WhatsApp group '{group.get('name')}'.")
            if not re.search(r"\d{6,}", to):
                names = ", ".join(f"'{g.get('name')}'" for g in groups) or "none"
                return ToolResult.fail(f"'{to}' is not a group agents may post to (open to agents: {names}) and not a phone number. "
                                       "The owner opens groups under Groups on the console's WhatsApp page.")
            number = re.sub(r"\D", "", to)
            if len(number) < 6:
                return ToolResult.fail("Give the phone number with country code, e.g. 6281234567890, or 'me'.")
            people = {c.get("phone"): c for c in state.get("designated") or []}
            if number not in people:
                return ToolResult.fail(f"+{number} is not a designated contact, and agents may only message the owner and designated "
                                       "contacts. Ask the owner to add it under Designated contacts on the console's WhatsApp page.")
            _call("/whatsapp/internal/send", {"agentId": account["agent"], "chat": f"{number}@s.whatsapp.net", "text": text})
            who = people[number].get("name") or f"+{number}"
            if people[number].get("mode") == "reply":
                return ToolResult.success(f"Sent to {who} (+{number}) on WhatsApp. They may reply to you directly.")
            return ToolResult.success(f"Sent to {who} (+{number}) on WhatsApp. If they reply, Lorium tells the owner and you are told "
                                      "what they said; you will not answer them.")
        except Exception as e:
            logger.warning(f"[whatsapp] {action} failed: {e}")
            return ToolResult.fail(f"WhatsApp: {e}")
