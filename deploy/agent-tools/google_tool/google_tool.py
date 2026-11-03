"""google: the owner's Gmail and Google Calendar (agent/lrm_google.py).

Owner only (agent/lrm_owner.py). Sending mail and inviting people wait for the
owner's approval (agent/lrm_activity.py). Installed by the LRMagent image.
"""

import json

from agent.tools.base_tool import BaseTool, ToolResult


class GoogleTool(BaseTool):
    name: str = "google"
    description: str = (
        "The owner's Gmail and Google Calendar. Use it when they ask about their email, inbox, a message from someone, "
        "their schedule, meetings, or to draft/send mail or add an event. Actions: 'gmail_search' (query in Gmail search "
        "syntax, e.g. 'is:unread newer_than:2d', 'from:budi invoice'), 'gmail_read' (id), 'gmail_draft' and 'gmail_send' "
        "(to, subject, body, optional cc, reply_to_id to answer a message in its thread), 'calendar_list' (days from today, or "
        "start/end ISO times, optional query), 'calendar_create' (title, start, end, optional description, location, "
        "attendees, all_day). Prefer gmail_draft unless the user clearly asked to send. Times are Asia/Jakarta unless given."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["gmail_search", "gmail_read", "gmail_draft", "gmail_send", "calendar_list", "calendar_create"]},
            "query": {"type": "string"}, "limit": {"type": "integer"}, "id": {"type": "string"},
            "to": {"type": "string"}, "cc": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
            "reply_to_id": {"type": "string"},
            "days": {"type": "integer"}, "start": {"type": "string", "description": "ISO date or date-time"}, "end": {"type": "string"},
            "title": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}}, "all_day": {"type": "boolean"},
        },
        "required": ["action"],
    }

    def execute(self, args: dict) -> ToolResult:
        from agent import lrm_google as g
        from agent.lrm_owner import REFUSAL, owner_session
        allowed, _ = owner_session(getattr(getattr(self, "model", None), "session_id", ""))
        if not allowed:
            return ToolResult.fail(REFUSAL)
        a = args.get("action")
        try:
            if a == "gmail_search":
                out = g.gmail_search(args.get("query") or "is:unread", args.get("limit") or 10)
            elif a == "gmail_read":
                out = g.gmail_read(args["id"])
            elif a in ("gmail_draft", "gmail_send"):
                if not args.get("to") or not args.get("body"):
                    return ToolResult.fail("Give 'to' and 'body' (and a subject unless replying).")
                fn = g.gmail_draft if a == "gmail_draft" else g.gmail_send
                out = fn(args["to"], args.get("subject", ""), args["body"], args.get("cc", ""), args.get("reply_to_id", ""))
            elif a == "calendar_list":
                out = g.calendar_list(args.get("start", ""), args.get("end", ""), args.get("days") or 1, args.get("query", ""))
            elif a == "calendar_create":
                if not args.get("title") or not args.get("start"):
                    return ToolResult.fail("Give a title and a start time.")
                out = g.calendar_create(args["title"], args["start"], args.get("end", ""), args.get("description", ""),
                                        args.get("location", ""), args.get("attendees") or [], bool(args.get("all_day")))
            else:
                return ToolResult.fail("Unknown action.")
        except KeyError as e:
            return ToolResult.fail(f"Missing {e}.")
        except Exception as e:
            return ToolResult.fail(f"Google: {e}")
        return ToolResult.success(json.dumps(out, ensure_ascii=False, indent=1))
