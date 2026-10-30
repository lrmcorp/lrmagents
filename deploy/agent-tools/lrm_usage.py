"""Token usage per agent, model and channel, for the LRMagent Usage page.

Every model call already reports its usage to the agent loop
(``_record_stream_usage``); the LRMagent image hooks that one place, so console
chats, WhatsApp, other channels, scheduled tasks and sub-agents are all counted.
Totals are kept per day in a small SQLite file next to config.json.
Installed as agent/lrm_usage.py by the LRMagent image.
"""

import datetime
import json
import os
import sqlite3
import threading

from common.log import logger

_lock = threading.Lock()
_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL, channel TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0, prompt INTEGER NOT NULL DEFAULT 0,
    completion INTEGER NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, agent, model, channel)
)"""


def _db_path():
    from config import get_data_root
    return os.path.join(get_data_root(), "lrm_usage.sqlite")


def _connect():
    global _ready
    conn = sqlite3.connect(_db_path(), timeout=5)
    if not _ready:
        conn.execute(_SCHEMA)
        _ready = True
    return conn


def _int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def record(executor, snapshot):
    """Add one model call's usage. Never raises: usage stats must not break a reply."""
    try:
        model = getattr(executor, "model", None)
        agent = str(getattr(model, "agent_id", "") or "default")
        name = str(getattr(model, "model", "") or "unknown")
        channel = str(getattr(model, "channel_type", "") or "other")
        row = (datetime.date.today().isoformat(), agent, name, channel,
               _int(snapshot.get("calls")) or 1, _int(snapshot.get("prompt_tokens")),
               _int(snapshot.get("completion_tokens")), _int(snapshot.get("prompt_cache_hit_tokens")))
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT INTO usage VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(day, agent, model, channel) DO UPDATE SET "
                "calls = calls + excluded.calls, prompt = prompt + excluded.prompt, "
                "completion = completion + excluded.completion, cached = cached + excluded.cached", row)
    except Exception as e:
        logger.debug(f"[lrm_usage] record failed: {e}")


def prices():
    """USD per million tokens, from the LRMAGENT_PRICES setting: {"model-prefix": [input, output]}."""
    try:
        raw = json.loads(os.environ.get("LRMAGENT_PRICES") or "{}")
        return {str(k): (float(v[0]), float(v[1])) for k, v in raw.items() if isinstance(v, (list, tuple)) and len(v) == 2}
    except Exception:
        return {}


def _price_for(model, table):
    # Longest matching prefix, so "claude-sonnet-5-5" can override "claude-sonnet".
    best = max((k for k in table if model.startswith(k)), key=len, default=None)
    return table.get(best) if best else None


def summary(days=30):
    since = (datetime.date.today() - datetime.timedelta(days=max(1, days) - 1)).isoformat()
    table = prices()
    try:
        with _lock, _connect() as conn:
            rows = conn.execute(
                "SELECT day, agent, model, channel, calls, prompt, completion, cached FROM usage "
                "WHERE day >= ? ORDER BY day DESC, agent, model", (since,)).fetchall()
    except Exception as e:
        logger.warning(f"[lrm_usage] read failed: {e}")
        rows = []
    out = []
    for day, agent, model, channel, calls, prompt, completion, cached in rows:
        price = _price_for(model, table)
        cost = round((prompt * price[0] + completion * price[1]) / 1_000_000, 4) if price else None
        out.append({"day": day, "agent": agent, "model": model, "channel": channel, "calls": calls,
                    "prompt": prompt, "completion": completion, "cached": cached, "cost": cost})
    return {"since": since, "rows": out, "priced_models": sorted(table)}
