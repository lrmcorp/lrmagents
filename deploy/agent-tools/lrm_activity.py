"""Activity log and approvals for every tool call an agent makes.

``gate`` runs just before a tool runs (hooked into agent_stream._execute_tool by
the LRMagent image). A risky call (deleting files, force-pushing, shutting
things down, changing keys, paying or submitting on a website) waits for the
owner: Approve/Deny in the console chat or on the Activity page, or "YES CODE" /
"NO CODE" on the owner's own WhatsApp chat. Unanswered requests expire and count
as refused. ``record`` writes every call to the activity log afterwards.
Installed as agent/lrm_activity.py by the LRMagent image.
"""

import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.request

from common.log import logger

TIMEOUT = int(os.environ.get("LRMAGENT_APPROVAL_TIMEOUT", "600"))
GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MODES = ("risky", "always", "off")
KEEP_DAYS = 90

_lock = threading.Lock()
_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, agent TEXT, session TEXT, channel TEXT,
    tool TEXT, summary TEXT, status TEXT, duration REAL, approval TEXT
);
CREATE INDEX IF NOT EXISTS activity_ts ON activity (ts);
CREATE TABLE IF NOT EXISTS approvals (
    code TEXT PRIMARY KEY, ts REAL NOT NULL, agent TEXT, session TEXT, channel TEXT, tool TEXT,
    summary TEXT, reason TEXT, status TEXT NOT NULL, decided_ts REAL, decided_via TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def _db_path():
    from config import get_data_root
    return os.path.join(get_data_root(), "lrm_activity.sqlite")


def _connect():
    global _ready
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    if not _ready:
        conn.executescript(_SCHEMA)
        _ready = True
    return conn


# ---- what counts as risky -------------------------------------------------------

_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|sk-ant-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
                     r"|xox[abp]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}"
                     r"|(?i:(?:password|passwd|token|secret|api[_-]?key)\s*[=:]\s*)\S+)")

_RISKY_COMMANDS = [
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+", "deletes files"),
    (r"\b(find\b[^|;&]*-delete|shred|wipefs|mkfs(\.\w+)?|dd\s+if=)", "erases data"),
    (r"\bgit\s+(push\b[^;&|]*(--force|-f\b|--delete|:)|reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D)", "rewrites or deletes git history"),
    (r"\b(shutdown|reboot|poweroff|halt|init\s+[06])\b", "shuts the machine down"),
    (r"\b(kill(all)?\s+-9|pkill\b|systemctl\s+(stop|disable|restart)|service\s+\S+\s+stop)", "stops running services"),
    (r"\bdocker\s+(rm|rmi|kill|stop|system\s+prune|volume\s+(rm|prune)|compose\s+down)", "stops or removes containers"),
    (r"\b(chmod|chown)\s+-R\b", "changes permissions recursively"),
    (r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b", "runs a script from the internet"),
    (r"\bsudo\b", "runs as administrator"),
    (r"(?i)\b(drop\s+(table|database|schema)|truncate\s+table|delete\s+from)\b", "deletes database data"),
    (r">\s*/dev/sd[a-z]|>\s*/etc/", "overwrites system files"),
    (r"\bcrontab\s+-r\b", "removes scheduled jobs"),
]
_RISKY_WEB = re.compile(r"(?i)\b(buy|purchase|pay|payment|checkout|place order|order now|transfer|send money|delete|remove account|"
                        r"unsubscribe|submit|publish|post|confirm|sign|subscribe)\b")


def redact(text, limit=400):
    text = _SECRET.sub("[secret]", str(text or ""))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize(tool, args):
    args = args or {}
    if tool == "bash":
        return redact(args.get("command", ""))
    if tool in ("write", "edit", "read", "ls", "search_files"):
        return redact(args.get("path") or args.get("file_path") or args.get("pattern") or json.dumps(args, ensure_ascii=False))
    if tool == "browser":
        bits = [str(args.get(k)) for k in ("action", "url", "selector", "text") if args.get(k)]
        if args.get("ref") is not None:
            bits.append(f"element {args.get('ref')}")
        return redact(" · ".join(bits) or json.dumps(args, ensure_ascii=False))
    return redact(json.dumps(args, ensure_ascii=False, default=str))


_REF_LINE = re.compile(r"^\s*\[(\d+)\]\s+(.+)$", re.M)


def remember_refs(executor, result_text):
    """Element labels from a browser snapshot ("[12] button \"Place order\""), so a later
    click by number can be checked by what it says."""
    refs = {int(n): label[:200] for n, label in _REF_LINE.findall(str(result_text or ""))}
    if refs:
        executor._lrm_refs = refs


def risk(tool, args, mode, refs=None):
    """Why this call needs the owner's OK, or ''."""
    if mode == "off":
        return ""
    args = args or {}
    if tool == "bash":
        command = str(args.get("command", ""))
        for pattern, why in _RISKY_COMMANDS:
            if re.search(pattern, command):
                return why
        return "runs a command" if mode == "always" else ""
    if tool == "env_config":
        action = str(args.get("action", "")).lower()
        if action not in ("list", "get", "show", ""):
            return "changes API keys or settings"
        return ""
    if tool == "browser":
        action = str(args.get("action", "")).lower()
        if action in ("click", "type", "fill", "press", "submit", "select", "evaluate"):
            text = " ".join(str(args.get(k, "")) for k in ("selector", "text", "value", "key", "element", "description"))
            try:
                text += " " + (refs or {}).get(int(args.get("ref")), "")
            except (TypeError, ValueError):
                pass
            if _RISKY_WEB.search(text) or action in ("submit", "evaluate"):
                return "acts on a website (submit, buy, post or delete)"
            return "acts on a website" if mode == "always" else ""
        return ""
    if mode == "always" and tool in ("write", "edit", "evolution_undo"):
        return "changes a file"
    return ""


# ---- settings ---------------------------------------------------------------------

def get_mode():
    try:
        with _lock, _connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='mode'").fetchone()
        return row["value"] if row and row["value"] in MODES else "risky"
    except Exception:
        return "risky"


def set_mode(mode):
    if mode not in MODES:
        raise ValueError("mode must be risky, always or off")
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('mode', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))


# ---- approvals ------------------------------------------------------------------------

def _where(executor):
    model = getattr(executor, "model", None)
    return (str(getattr(model, "agent_id", "") or "default"), str(getattr(model, "session_id", "") or ""),
            str(getattr(model, "channel_type", "") or ""))


def _new_code(conn):
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        if not conn.execute("SELECT 1 FROM approvals WHERE code=? AND status='pending'", (code,)).fetchone():
            return code
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def _agent_name(agent_id):
    try:
        from agent.registry import get_agent_registry
        return get_agent_registry().get(agent_id, require_enabled=False).name or agent_id
    except Exception:
        return agent_id


def _notify_owner(agent_id, text):
    """Message the owner's own WhatsApp chat. Best effort."""
    try:
        from config import conf
        token = str(conf().get("external_api_token") or "")
        if not token:
            return
        request = urllib.request.Request(
            f"{GATEWAY_URL}/whatsapp/internal/notify-owner",
            data=json.dumps({"agentId": agent_id, "text": text}).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=15).read()
    except Exception as e:
        logger.info(f"[Approvals] could not message the owner on WhatsApp: {e}")


def decide(code, decision, via="console"):
    """Approve or refuse a pending request. Returns the request, or None."""
    code = str(code or "").strip().upper()
    status = "approved" if decision in ("approve", "approved", "yes", True) else "denied"
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE code=? AND status='pending'", (code,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE approvals SET status=?, decided_ts=?, decided_via=? WHERE code=?", (status, time.time(), via, code))
        out = dict(row)
    out.update(status=status, decided_via=via)
    return out


def pending():
    _expire()
    with _lock, _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY ts DESC")]


def _expire():
    with _lock, _connect() as conn:
        conn.execute("UPDATE approvals SET status='expired', decided_ts=? WHERE status='pending' AND ts < ?",
                     (time.time(), time.time() - TIMEOUT - 30))


def gate(executor, tool_name, arguments, tool_call_id):
    """None to let the call run; otherwise the refusal the model reads. Never raises."""
    try:
        why = risk(tool_name, arguments, get_mode(), getattr(executor, "_lrm_refs", None))
        if not why:
            return None
        agent_id, session_id, channel = _where(executor)
        summary = summarize(tool_name, arguments)
        with _lock, _connect() as conn:
            code = _new_code(conn)
            conn.execute("INSERT INTO approvals (code, ts, agent, session, channel, tool, summary, reason, status) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                         (code, time.time(), agent_id, session_id, channel, tool_name, summary, why))
        minutes = max(1, TIMEOUT // 60)
        executor._emit_event("tool_execution_progress", {
            "tool_call_id": tool_call_id, "tool_name": tool_name,
            "message": f"⏳ Waiting for your approval (code {code}): this {why}. Approve or deny below, on the Activity page, "
                       f"or reply YES {code} / NO {code} on your WhatsApp. Expires in {minutes} min.",
        })
        if channel != "web":
            _notify_owner(agent_id, f"🔐 Approval needed ({_agent_name(agent_id)}): it wants to {why}.\n\n{tool_name}: {summary}\n\n"
                                    f"Reply YES {code} to allow or NO {code} to refuse. Expires in {minutes} min.")
        deadline = time.time() + TIMEOUT
        cancel = getattr(executor, "cancel_event", None)
        status = "pending"
        while time.time() < deadline:
            if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                status = "cancelled"
                break
            with _lock, _connect() as conn:
                status = conn.execute("SELECT status FROM approvals WHERE code=?", (code,)).fetchone()["status"]
            if status != "pending":
                break
            time.sleep(1)
        if status == "pending":
            with _lock, _connect() as conn:
                conn.execute("UPDATE approvals SET status='expired', decided_ts=? WHERE code=?", (time.time(), code))
            status = "expired"
        executor._lrm_approval = f"{status} {code}"
        if status == "approved":
            return None
        if status == "denied":
            return f"The owner refused this action (approval {code}). Do not try it another way; tell them what you would need instead."
        if status == "cancelled":
            return "The conversation was stopped while this action waited for approval."
        return (f"Nobody approved this action within {minutes} minutes (approval {code}), so it did not run. "
                "Tell the owner what you wanted to do and ask them to approve it next time.")
    except Exception as e:
        logger.warning(f"[Approvals] check skipped for {tool_name}: {e}")
        return None


# ---- the log ---------------------------------------------------------------------------

_last_prune = [0.0]


def record(executor, tool_name, arguments, result):
    """Log one finished tool call. Never raises."""
    try:
        agent_id, session_id, channel = _where(executor)
        approval = getattr(executor, "_lrm_approval", "") or ""
        executor._lrm_approval = ""
        status = str((result or {}).get("status", "") or "")
        if tool_name == "browser" and status == "success":
            remember_refs(executor, (result or {}).get("result"))
        with _lock, _connect() as conn:
            conn.execute("INSERT INTO activity (ts, agent, session, channel, tool, summary, status, duration, approval) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (time.time(), agent_id, session_id, channel, tool_name, summarize(tool_name, arguments), status,
                          float((result or {}).get("execution_time") or 0), approval))
            if time.time() - _last_prune[0] > 3600:
                _last_prune[0] = time.time()
                cutoff = time.time() - KEEP_DAYS * 86400
                conn.execute("DELETE FROM activity WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM approvals WHERE ts < ? AND status != 'pending'", (cutoff,))
    except Exception as e:
        logger.debug(f"[Activity] not recorded: {e}")


def query(days=7, agent="", tool="", status="", q="", limit=500):
    sql = "SELECT * FROM activity WHERE ts >= ?"
    params = [time.time() - max(1, min(int(days), KEEP_DAYS)) * 86400]
    for col, value in (("agent", agent), ("tool", tool), ("status", status)):
        if value:
            sql += f" AND {col} = ?"
            params.append(value)
    if q:
        sql += " AND (summary LIKE ? OR session LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(max(1, min(int(limit), 2000)))
    with _lock, _connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
        decided = [dict(r) for r in conn.execute(
            "SELECT * FROM approvals WHERE status != 'pending' AND ts >= ? ORDER BY ts DESC LIMIT 100", (params[0],))]
    return rows, decided
