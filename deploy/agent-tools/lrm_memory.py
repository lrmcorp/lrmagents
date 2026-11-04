"""Every chat, summarised into the Vault as memory.

Upstream only writes memory when a long chat is trimmed, so most conversations
never reach it. This loop looks for chats that have gone quiet (30 minutes by
default) with something new in them, asks the model for a structured summary
and keeps one note per chat in the Vault, ``memory/chats/<YYYY-MM>/<date> <title>.md``:
summary, key points, decisions, to-dos and what is worth remembering, with
properties and [[links]] Obsidian understands. The note is rewritten when the
chat continues later, so it always covers the whole conversation.

The notes are part of the agent's memory, so memory_search finds them. They are
the owner's: WhatsApp contacts and API callers can't search or open them
(see ``hidden_from``).

Summaries use the main model and its fallbacks; when none answers and one of the
owner's plans is signed in (Codex, Claude Code, ...), that plan writes it.
A chat that could not be summarised is tried again on a later pass.
"""
import datetime
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request

from common.log import logger

IDLE_MIN = int(os.environ.get("LRMAGENT_CHAT_MEMORY_IDLE_MIN", "30") or 30)
ENABLED = os.environ.get("LRMAGENT_CHAT_MEMORY", "on").lower() not in ("0", "off", "false", "no")
PASS_EVERY = 10 * 60
MAX_PER_PASS = 12
MAX_CHARS = 40_000
FOLDER = "chats"
SKIP_PREFIXES = ("openai:conversation:lrm-brief:", "openai:conversation:lrm-report:", "openai:conversation:lrm-review:", "lrm-memory:")
WHATSAPP = "openai:conversation:whatsapp:"

PROMPT = """You keep the owner's memory for LRMagent (Liando Reyfort Management). Read the conversation below and
write a note about it for the owner's knowledge vault. Reply with JSON only, no code fences:

{{"skip": false,
  "title": "short descriptive title, max 60 characters",
  "summary": "2-4 sentences: what the chat was about and how it ended",
  "points": ["key facts, findings or answers worth keeping"],
  "decisions": ["decisions made"],
  "todos": ["open tasks or follow-ups, each one actionable"],
  "remember": ["lasting facts about the owner or the people involved: preferences, plans, contacts, numbers, dates"],
  "people": ["names of people or organisations that matter in it"],
  "topics": ["2-5 short lowercase topic tags"],
  "personal": false}}

"personal" is true when the chat is mainly about the owner's private life (family, health, money,
relationships, home, personal plans or feelings) rather than work or the business.
Write dates as DD-MM-YYYY (e.g. 04-11-2026). Rules: write in English even when the chat is in another language (keep names, quotes and figures as they are).
Be specific: names, amounts, dates, links. Leave a list empty when there is nothing for it. Never copy passwords,
API keys, codes or other secrets. If the chat has nothing worth keeping (greetings, a test, small talk), reply
{{"skip": true}}.

Chat ({where}, {when}):
{conversation}"""


# ---- state ----------------------------------------------------------------------------

def _state_file():
    from config import get_data_root
    return os.path.join(get_data_root(), "lrm_chat_memory.json")


def _load_state():
    try:
        with open(_state_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    path = _state_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


# ---- reading chats ----------------------------------------------------------------------

def _secs(value):
    value = int(value or 0)
    return value / 1000 if value > 10_000_000_000 else value


def _quiet_sessions(store, state, agent_key):
    """(session_id, channel_type, title, last_active, max_seq) of chats that went quiet with news."""
    cutoff = time.time() - IDLE_MIN * 60
    with sqlite3.connect(f"file:{store._db_path}?mode=ro", uri=True, timeout=10) as db:
        rows = db.execute(
            "SELECT s.session_id, s.channel_type, s.title, s.last_active, MAX(m.seq) FROM sessions s "
            "JOIN messages m ON m.agent_id = s.agent_id AND m.session_id = s.session_id "
            "WHERE s.agent_id = ? GROUP BY s.session_id ORDER BY s.last_active DESC LIMIT 400",
            (store._agent_id,)).fetchall()
    out = []
    for session_id, channel, title, last_active, max_seq in rows:
        if str(session_id).startswith(SKIP_PREFIXES) or _secs(last_active) > cutoff:
            continue
        done = state.get(f"{agent_key}|{session_id}", {})
        if (max_seq or 0) <= done.get("seq", -1):
            continue
        if done.get("fails", 0) >= 5 and time.time() - done.get("at", 0) < 24 * 3600:
            continue  # keeps failing: once a day is enough
        out.append((session_id, channel or "", title or "", _secs(last_active), max_seq or 0))
    return out


def _conversation(store, session_id):
    from agent.memory.conversation_store import _message_is_scheduled_marker, _message_plain_text
    with sqlite3.connect(f"file:{store._db_path}?mode=ro", uri=True, timeout=10) as db:
        rows = db.execute("SELECT role, content, created_at FROM messages WHERE agent_id = ? AND session_id = ? ORDER BY seq",
                          (store._agent_id, session_id)).fetchall()
    lines, users, first = [], 0, None
    for role, content, created in rows:
        if role not in ("user", "assistant") or _message_is_scheduled_marker(content):
            continue
        text = _message_plain_text(content)
        if not text:
            continue
        first = first or _secs(created)
        if role == "user":
            users += len(text)
        text = text if len(text) <= 3000 else text[:3000] + " […]"
        lines.append(f"{'Owner/user' if role == 'user' else 'Assistant'}: {text}")
    body = "\n\n".join(lines)
    if len(body) > MAX_CHARS:
        body = "[…earlier part left out…]\n\n" + body[-MAX_CHARS:]
    return body, users, first


def _where(session_id, channel):
    if session_id.startswith(WHATSAPP):
        chat = session_id[len(WHATSAPP):].split(":", 1)[-1]
        number = chat.split("@")[0]
        return "whatsapp", (f"WhatsApp group {number}" if chat.endswith("@g.us") else f"WhatsApp with +{number}"), number
    if session_id.startswith("openai:"):
        return "api", "the chat API", ""
    return channel or "web", "the LRMagent console", ""


# ---- asking a model ----------------------------------------------------------------------

def _ask_main(prompt):
    # Name this job so the plans fallback accepts it (owner work, like the brief).
    from types import SimpleNamespace
    from agent import lrm_plans
    token = lrm_plans.begin_run(SimpleNamespace(session_id=f"lrm-memory:{datetime.date.today().isoformat()}"))
    try:
        return _ask_main_inner(prompt)
    finally:
        lrm_plans.end_run(token)


def _ask_main_inner(prompt):
    from agent.memory.summarizer import MemoryFlushManager
    from agent.protocol.models import LLMRequest
    from bridge.agent_bridge import AgentLLMModel
    from bridge.bridge import Bridge
    model = AgentLLMModel(Bridge())
    try:
        chain = model.fallback_config().get("chain") or []
    except Exception:
        chain = []
    for attempt in range(1 + len(chain)):
        if attempt and not model.use_fallback():
            break
        try:
            response = model.call(LLMRequest(messages=[{"role": "user", "content": prompt}], stream=False, temperature=0.2))
            text = MemoryFlushManager._extract_response_text(response)
            if text and text.strip():
                return text
        except Exception as e:
            logger.info(f"[ChatMemory] {getattr(model, 'model', 'model')} could not summarise: {e}")
    return ""


def _ask_plan(prompt):
    from agent import lrm_plans
    names, _ = lrm_plans.models()
    if not names:
        return ""
    day = datetime.date.today().isoformat()
    request = urllib.request.Request(
        f"{lrm_plans.API_BASE}/chat/completions", method="POST",
        data=json.dumps({"model": names[0], "stream": False, "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": f"Bearer {lrm_plans.provider()['api_key']}", "Content-Type": "application/json",
                 "X-LRM-Session": f"lrm-memory:{day}"})
    try:
        with urllib.request.urlopen(request, timeout=1200) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception as e:
        logger.info(f"[ChatMemory] plan {names[0]} could not summarise: {e}")
        return ""


def _parse(raw):
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ---- writing notes -----------------------------------------------------------------------

def _clean(value, limit=400):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _items(data, key, limit=12):
    values = data.get(key) or []
    if not isinstance(values, list):
        values = [values]
    return [v for v in (_clean(x) for x in values[:limit]) if v]


def _yaml(value):
    return json.dumps(value, ensure_ascii=False)


def _filename(title):
    name = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', "", title).strip(" .")
    return (name or "Chat")[:60]


def render(data, *, session_id, channel, where, contact, agent_id, started, updated):
    title = _clean(data.get("title"), 60) or "Chat"
    topics = [re.sub(r"[^a-z0-9/_-]+", "-", t.lower()).strip("-") for t in _items(data, "topics", 5)]
    topics = [t for t in topics if t]
    people = _items(data, "people", 10)
    day = datetime.datetime.fromtimestamp(started).strftime("%Y-%m-%d")
    props = [
        "---", "type: chat", f"date: {day}",
        f"updated: {datetime.datetime.fromtimestamp(updated).strftime('%Y-%m-%d %H:%M')}",
        f"channel: {channel}", f"where: {_yaml(where)}",
    ]
    if contact:
        props.append(f"contact: {_yaml(contact)}")
    if data.get("personal") is True:
        props.append("personal: true")
    props += [f"agent: {_yaml(agent_id or 'default')}", f"session: {_yaml(session_id)}",
              f"tags: {_yaml(['chat'] + topics)}"]
    if people:
        props.append(f"people: {_yaml(people)}")
    props.append("---")
    out = props + ["", f"# {title}", "", _clean(data.get("summary"), 1200) or "_No summary._", ""]
    for heading, key, box in (("Key points", "points", False), ("Decisions", "decisions", False),
                              ("To-dos", "todos", True), ("Remember", "remember", False)):
        values = _items(data, key)
        if values:
            out += [f"## {heading}", ""] + [f"- [ ] {v}" if box else f"- {v}" for v in values] + [""]
    if people:
        out += ["## People", "", " · ".join(f"[[{p}]]" for p in people), ""]
    out += [f"_Summarised by LRMagent from {where}._", ""]
    return title, "\n".join(out)


def _hidden_rel(rel):
    rel = rel.replace("\\", "/").lstrip("./")
    return rel.startswith(f"memory/{FOLDER}/") or rel.startswith(f"{FOLDER}/")


def hidden_from(session_id, path):
    """True when ``path`` is a chat note and ``session_id`` is not the owner's."""
    path = str(path or "").replace("\\", "/")
    if f"/memory/{FOLDER}/" not in f"/{path.lstrip('/')}" and not _hidden_rel(path):
        return False
    if not str(session_id or "").startswith("openai:"):
        return False
    from agent.lrm_owner import owner_session
    return not owner_session(session_id)[0]


# ---- the loop ---------------------------------------------------------------------------

def summarise_pass():
    from agent.memory.conversation_store import get_conversation_store
    from agent.registry import get_agent_registry
    state = _load_state()
    done = 0
    for profile in get_agent_registry().list(include_disabled=False):
        try:
            store = get_conversation_store(profile.workspace)
            sessions = _quiet_sessions(store, state, profile.id)
        except Exception as e:
            logger.warning(f"[ChatMemory] cannot read chats of {profile.id}: {e}")
            continue
        for session_id, channel_type, stored_title, last_active, max_seq in sessions:
            if done >= MAX_PER_PASS:
                _save_state(state)
                return done
            key = f"{profile.id}|{session_id}"
            entry = state.get(key, {})
            body, user_chars, first = _conversation(store, session_id)
            if user_chars < 12:
                state[key] = {**entry, "seq": max_seq, "at": time.time(), "skipped": True}
                continue
            channel, where, contact = _where(session_id, channel_type)
            when = datetime.datetime.fromtimestamp(first or last_active).strftime("%d-%m-%Y")
            prompt = PROMPT.format(where=where, when=when, conversation=body)
            data = _parse(_ask_main(prompt)) or _parse(_ask_plan(prompt))
            if data is None:
                state[key] = {**entry, "fails": entry.get("fails", 0) + 1, "at": time.time()}
                _save_state(state)
                continue
            done += 1
            if data.get("skip"):
                # Nothing worth a note (or nothing new for the one already there).
                state[key] = {**entry, "seq": max_seq, "at": time.time(), "fails": 0, **({} if entry.get("note") else {"skipped": True})}
                continue
            title, text = render(data, session_id=session_id, channel=channel, where=where, contact=contact,
                                 agent_id=profile.id, started=first or last_active, updated=last_active)
            # Personal chats (any agent) are kept by the personal assistant only.
            owner_ws = profile.workspace
            target_agent = profile.id
            try:
                from agent import lrm_personal
                pws = lrm_personal.workspace()
                if pws and (data.get("personal") is True or profile.id == lrm_personal.ID):
                    owner_ws, target_agent = str(pws), lrm_personal.ID
            except Exception:
                pass
            root = os.path.join(owner_ws, "memory")
            rel = entry.get("note")
            if rel and entry.get("root") and entry.get("root") != root and os.path.exists(os.path.join(entry["root"], rel)):
                try:
                    os.remove(os.path.join(entry["root"], rel))  # moved: it turned out personal (or not)
                except OSError:
                    pass
                rel = None
            if not rel or not os.path.exists(os.path.join(root, rel)):
                started = datetime.datetime.fromtimestamp(first or last_active)
                folder = os.path.join(FOLDER, started.strftime("%Y-%m"))
                base = f"{started.strftime('%d-%m-%Y')} {_filename(title)}"  # the owner's date format
                rel, n = os.path.join(folder, base + ".md"), 2
                while os.path.exists(os.path.join(root, rel)):
                    rel, n = os.path.join(folder, f"{base} {n}.md"), n + 1
            full = os.path.join(root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            tmp = full + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, full)
            state[key] = {"seq": max_seq, "at": time.time(), "note": rel, "root": root}
            _save_state(state)
            logger.info(f"[ChatMemory] saved {rel} for {target_agent}")
            try:
                from channel.web.api.workspace import _mark_memory_dirty
                _mark_memory_dirty(target_agent)
            except Exception:
                pass
    _save_state(state)
    return done


def _loop():
    time.sleep(90)
    while True:
        try:
            summarise_pass()
        except Exception as e:
            logger.warning(f"[ChatMemory] pass failed: {e}")
        time.sleep(PASS_EVERY)


_started = False


def start():
    global _started
    if ENABLED and not _started:
        _started = True
        threading.Thread(target=_loop, name="lrm-chat-memory", daemon=True).start()
