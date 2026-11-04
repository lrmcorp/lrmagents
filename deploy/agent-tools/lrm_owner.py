"""Is this conversation the owner's? For tools that reach the owner's personal
accounts (Gmail, Calendar): the console, the WhatsApp number's own chat
("Message yourself") and LRMagent's own morning brief are; other WhatsApp
contacts and API callers are not.
Installed as agent/lrm_owner.py by the LRMagent image.
"""

import json
import os
import urllib.parse
import urllib.request

from common.log import logger

GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
WHATSAPP_PREFIX = "openai:conversation:whatsapp:"
BRIEF_PREFIX = "openai:conversation:lrm-brief:"
# The console's voice page (signed-in owner only) and reports one agent asks of another for the owner.
OWNER_PREFIXES = (BRIEF_PREFIX, "openai:conversation:lrm-voice:", "openai:conversation:lrm-report:", "openai:conversation:lrm-review:")
REFUSAL = ("This uses the owner's personal accounts, so only the owner can use it: from the LRMagent console or the "
           "WhatsApp number's own chat (Message yourself).")


def owner_session(session_id):
    """(allowed, whatsapp target {agentId, chat} or None)."""
    session_id = str(session_id or "")
    if not session_id.startswith("openai:"):
        return True, None
    if session_id.startswith(OWNER_PREFIXES):
        return True, None
    if not session_id.startswith(WHATSAPP_PREFIX):
        return False, None
    agent_id, _, chat = session_id[len(WHATSAPP_PREFIX):].partition(":")
    try:
        from config import conf
        token = str(conf().get("external_api_token") or "")
        request = urllib.request.Request(f"{GATEWAY_URL}/whatsapp/internal/owner?" + urllib.parse.urlencode({"agent": agent_id, "chat": chat}),
                                         headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=10) as r:
            owner = json.loads(r.read()).get("owner") is True
    except Exception as e:
        logger.warning(f"[owner] check failed: {e}")
        owner = False
    return owner, ({"agentId": agent_id, "chat": chat} if owner else None)
