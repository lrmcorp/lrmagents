"""chat_monitor: reports on a branch's WhatsApp, which LRMagent only reads (Monitor only).

The branch's agent (or Lorium, for any branch) gets the day's numbers, counted by the
gateway, and every customer conversation with the staff's replies, then writes the owner
a short report: new numbers, customer chats, complaints, reply times, chat quality, sales.
Owner's chats and the scheduled tasks they set up only.
"""
import datetime
import json
import os
import re
import urllib.parse
import urllib.request

from agent.tools.base_tool import BaseTool, ToolResult
from config import conf

GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
MAX_CHARS = 40000


def _get(path):
    token = str(conf().get("external_api_token") or "")
    req = urllib.request.Request(f"{GATEWAY_URL}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read() or b"{}").get("error")
        except Exception:
            why = None
        raise RuntimeError(why or f"gateway answered {e.code}")


def _day(text, today):
    text = str(text or "today").strip().lower()
    if text in ("today", "hari ini"):
        return today
    if text in ("yesterday", "kemarin"):
        return today - datetime.timedelta(days=1)
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", text)
    if m:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return datetime.date.fromisoformat(text)


class ChatMonitor(BaseTool):
    name: str = "chat_monitor"
    description: str = (
        "Reports on a branch's WhatsApp number that is set to Monitor only (LRMagent reads every chat there and never "
        "sends anything; never message those customers). Actions: 'list' (which numbers are monitored, by which agent), "
        "'report' (agent: the branch agent, default yourself; date: today, yesterday, DD-MM-YYYY, or from/to for a period). "
        "It returns the counted numbers (customer chats, new numbers, new ones not saved in the phone's contacts, "
        "unanswered chats, reply times) and every conversation. Read them and write the owner a short report as a title and "
        "points: *Chat report <branch> <DD-MM-YYYY>*; customer chats and new numbers (not saved in contacts); complaints "
        "(who, what, handled or not); unanswered chats; reply time (median, slow replies over 15 minutes); staff chat "
        "quality (late replies, unclear or impolite answers, missed questions, no follow-up); sales (chats that became a "
        "booking or purchase, and those that did not and the likely reason). Quote short examples. No greeting, no filler."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "report"]},
            "agent": {"type": "string", "description": "For 'report': the branch agent's id or name (default: yourself)"},
            "date": {"type": "string", "description": "For 'report': today, yesterday or DD-MM-YYYY"},
            "from": {"type": "string", "description": "For a period: first day (DD-MM-YYYY)"},
            "to": {"type": "string", "description": "For a period: last day (DD-MM-YYYY)"},
        },
        "required": ["action"],
    }

    def execute(self, args: dict) -> ToolResult:
        from agent.lrm_owner import REFUSAL, owner_session
        model = getattr(self, "model", None)
        if not owner_session(str(getattr(model, "session_id", "") or ""))[0]:
            return ToolResult.fail(REFUSAL)
        from agent.registry import get_agent_registry
        reg = get_agent_registry()
        names = {p.id: p.name for p in reg.list()}
        action = (args.get("action") or "").strip()
        try:
            state = _get("/whatsapp/internal/status")
            monitored = [a for a in state.get("accounts") or [] if a.get("mode") == "monitor"]
            if action == "list":
                if not monitored:
                    return ToolResult.success("No number is set to Monitor only yet (WhatsApp page → an agent's card → Mode).")
                return ToolResult.success("Monitored WhatsApp numbers:\n" + "\n".join(
                    f"- {names.get(a['agent'], a['agent'])} [id {a['agent']}]: +{a.get('phone') or '?'} ({a.get('status')})" for a in monitored))
            if action != "report":
                return ToolResult.fail("action must be 'list' or 'report'.")
            wanted = str(args.get("agent") or getattr(model, "agent_id", "") or "").strip().lower()
            agent = next((a["agent"] for a in monitored if a["agent"].lower() == wanted or names.get(a["agent"], "").lower() == wanted), None)
            if agent is None:
                if len(monitored) == 1 and not args.get("agent"):
                    agent = monitored[0]["agent"]
                else:
                    return ToolResult.fail("Which branch? Monitored: " + (", ".join(f"{names.get(a['agent'], a['agent'])} [id {a['agent']}]" for a in monitored) or "none"))
            today = datetime.date.today()
            first = _day(args.get("from") or args.get("date"), today)
            last = _day(args.get("to"), today) if args.get("to") else first
            q = urllib.parse.urlencode({"agent": agent, "from": first.isoformat(), "to": last.isoformat()})
            rep = _get(f"/whatsapp/internal/monitor?{q}")
        except Exception as e:
            return ToolResult.fail(f"Chat monitor: {e}")
        st = rep.get("stats") or {}
        period = first.strftime("%d-%m-%Y") + ("" if first == last else " to " + last.strftime("%d-%m-%Y"))
        lines = [f"Branch: {names.get(agent, agent)} · {period} ({rep.get('timezone')})",
                 f"Customer chats: {st.get('customerChats', 0)} · new numbers: {st.get('newNumbers', 0)} "
                 f"(not saved in contacts: {st.get('newNotSaved', 0)}) · unanswered now: {st.get('unanswered', 0)}",
                 f"Staff replies: {st.get('replies', 0)} · median wait: {st.get('medianReplyMinutes')} min · waits over 15 min: {st.get('slowReplies', 0)}",
                 "", "Conversations (newest numbers first; Customer / Staff lines with times):"]
        used = sum(len(x) for x in lines)
        shown = 0
        for c in rep.get("chats") or []:
            head = (f"\n## {c['contact']} {c['phone']} · {'NEW' if c['new'] else 'returning'}{'' if c['saved'] else ', not saved in contacts'} · "
                    f"customer {c['customerMessages']} / staff {c['staffMessages']} msgs · first reply "
                    f"{'none' if c['firstReplyMinutes'] is None else str(c['firstReplyMinutes']) + ' min'}"
                    + (f" · longest wait {c['longestWaitMinutes']} min" if c.get('longestWaitMinutes') else "")
                    + (f" · UNANSWERED for {c['unanswered']} min" if c.get('unanswered') else ""))
            body = "\n".join(c.get("transcript") or [])
            if used + len(head) + len(body) > MAX_CHARS:
                break
            lines += [head, body]
            used += len(head) + len(body)
            shown += 1
        rest = len(rep.get("chats") or []) - shown
        if rest > 0:
            lines.append(f"\n(+{rest} more conversations not shown; ask for a shorter period for all of them.)")
        if not rep.get("chats"):
            lines.append("No customer chats in this period.")
        return ToolResult.success("\n".join(lines))
