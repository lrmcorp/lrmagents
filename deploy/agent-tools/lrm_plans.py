"""The owner's signed-in AI plans (Codex, Claude Code, Gemini, Grok, DeepSeek) as chat models.

The teammates service answers OpenAI-style chat requests at /teammates/v1 by running the
signed-in CLI. LRMagent sees it as one extra custom provider, "Your plans", that is never
stored in config: it appears in the model picker only while at least one plan is signed in.
Each request carries the LRMagent session id, so every chat keeps its own CLI session and
the service can refuse chats that are not the owner's.
"""
import contextvars
import json
import os
import time
import urllib.request

from config import conf

PROVIDER_ID = "lrmplans"
BOT_TYPE = f"custom:{PROVIDER_ID}"
NAME = "Your plans"
TEAMMATES_URL = os.environ.get("TEAMMATES_URL", "http://teammates:8090").rstrip("/")
API_BASE = f"{TEAMMATES_URL}/teammates/v1"

_session = contextvars.ContextVar("lrm_plans_session", default="")
_cache = {"at": 0.0, "models": [], "labels": {}}


def provider():
    """The virtual custom-provider entry (credentials for custom:lrmplans)."""
    return {"id": PROVIDER_ID, "name": NAME, "api_key": str(conf().get("external_api_token") or ""),
            "api_base": API_BASE, "model": "codex", "headers": {}}


def models():
    """(model ids, labels) of the plans that are signed in; cached for a minute."""
    if time.time() - _cache["at"] < 60:
        return _cache["models"], _cache["labels"]
    found, labels = [], {}
    try:
        request = urllib.request.Request(f"{API_BASE}/models",
                                         headers={"Authorization": f"Bearer {provider()['api_key']}"})
        with urllib.request.urlopen(request, timeout=8) as response:
            for row in json.loads(response.read() or b"{}").get("data", []):
                if row.get("id"):
                    found.append(row["id"])
                    labels[row["id"]] = row.get("name") or row["id"]
    except Exception:
        # Service down: keep what we last knew, try again in 10 seconds.
        _cache["at"] = time.time() - 50
        return _cache["models"], _cache["labels"]
    _cache.update(at=time.time(), models=found, labels=labels)
    return found, labels


def catalog_group():
    found, labels = models()
    if not found:
        return None
    return {"id": BOT_TYPE, "label": {"zh": NAME, "en": NAME}, "models": found, "labels": labels}


def begin_run(model):
    """Remember which chat this agent run belongs to; returns a token for end_run."""
    return _session.set(str(getattr(model, "session_id", "") or ""))


def end_run(token):
    try:
        _session.reset(token)
    except Exception:
        pass


def headers_for(url):
    if url and str(url).startswith(API_BASE):
        return {"X-LRM-Session": _session.get()}
    return {}


# ---- plans as the last fallback ------------------------------------------------------
# When the main model and its configured fallbacks fail (out of credit, outage), the
# owner's own chats continue on a signed-in plan. Other chats never use the plans.
FALLBACK = os.environ.get("LRMAGENT_PLANS_FALLBACK", "on").lower() not in ("0", "off", "false", "no")
_owners = {}


def _is_owner(session_id):
    session_id = str(session_id or "")
    if not session_id.startswith("openai:"):
        return True
    hit = _owners.get(session_id)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    try:
        from agent.lrm_owner import owner_session
        owner = bool(owner_session(session_id)[0])
    except Exception:
        owner = False
    _owners[session_id] = (owner, time.time())
    return owner


def fallback_links(model_wrapper, chain):
    """Plan links to add at the end of ``chain`` for this run (may be empty)."""
    if not FALLBACK:
        return []
    found, _ = models()
    if not found:
        return []
    session = getattr(model_wrapper, "session_id", "") or _session.get()
    if not _is_owner(session):
        return []
    have = {(link.get("provider"), link.get("model")) for link in chain}
    return [{"provider": BOT_TYPE, "model": m} for m in found if (BOT_TYPE, m) not in have]
