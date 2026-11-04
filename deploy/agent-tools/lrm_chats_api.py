"""/api/lrm/chats: WhatsApp conversations of every agent, for the console's Chats page.

The console lists only the chats started in the console. This lists the WhatsApp ones
too (newest first, with the agent and the contact), and returns one conversation's
messages to read. Signed-in console only. Installed as channel/web/api/lrm_chats.py.
"""
import json
import sqlite3

import web

from channel.web.core._common import _require_auth

WHATSAPP = "openai:conversation:whatsapp:"


def _stores():
    from agent.memory.conversation_store import get_conversation_store
    from agent.registry import get_agent_registry
    for p in get_agent_registry().list():
        try:
            yield p, get_conversation_store(p.workspace)
        except Exception:
            continue


def _secs(v):
    v = int(v or 0)
    return v // 1000 if v > 10_000_000_000 else v


def _contact(session_id):
    chat = session_id[len(WHATSAPP):].split(":", 1)[-1]
    number = chat.split("@")[0]
    return ("group " + number) if chat.endswith("@g.us") else ("+" + number if chat.endswith("@s.whatsapp.net") else number)


def list_chats(limit=300):
    out, seen = [], set()
    for profile, store in _stores():
        key = (str(store._db_path), store._agent_id)
        if key in seen:
            continue
        seen.add(key)
        with sqlite3.connect(f"file:{store._db_path}?mode=ro", uri=True, timeout=10) as db:
            rows = db.execute("SELECT session_id, title, last_active, msg_count FROM sessions WHERE agent_id = ? AND session_id LIKE ? "
                              "ORDER BY last_active DESC LIMIT ?", (store._agent_id, WHATSAPP + "%", limit)).fetchall()
        for sid, title, last, count in rows:
            out.append({"agent": {"id": profile.id, "name": profile.name}, "session_id": sid, "title": title or "",
                        "contact": _contact(sid), "last_active": _secs(last), "messages": count or 0, "channel": "whatsapp"})
    out.sort(key=lambda r: r["last_active"], reverse=True)
    return out[:limit]


def messages(agent_id, session_id, limit=300):
    from agent.memory.conversation_store import _message_plain_text
    for profile, store in _stores():
        if profile.id != agent_id:
            continue
        with sqlite3.connect(f"file:{store._db_path}?mode=ro", uri=True, timeout=10) as db:
            rows = db.execute("SELECT role, content, created_at FROM messages WHERE agent_id = ? AND session_id = ? ORDER BY seq DESC LIMIT ?",
                              (store._agent_id, session_id, limit)).fetchall()
        msgs = []
        for role, content, created in reversed(rows):
            if role not in ("user", "assistant"):
                continue
            text = _message_plain_text(content)
            if text:
                msgs.append({"role": role, "text": text, "at": _secs(created)})
        return msgs
    return []


class LrmChatsHandler:
    def GET(self):
        _require_auth()
        q = web.input(agent="", session="")
        web.header("Content-Type", "application/json; charset=utf-8")
        if q.session:
            if not q.session.startswith(WHATSAPP):
                return json.dumps({"error": "not a WhatsApp chat"})
            return json.dumps({"messages": messages(q.agent, q.session)}, ensure_ascii=False)
        return json.dumps({"chats": list_chats()}, ensure_ascii=False)
