"""Deliver scheduled messages back to the WhatsApp chat they were set up in.

WhatsApp chats reach LRMagent through its OpenAI-compatible API (channel type
``openai_api``) with conversation ids ``whatsapp:<agent>:<chat jid>``, so the
scheduler has no channel to send through. This relay is that channel: it posts
to the WhatsApp gateway's internal send endpoint over the private Docker
network. Installed as channel/lrm_whatsapp_relay.py by the LRMagent image.
"""

import json
import os
import urllib.request

from common.log import logger
from config import conf

SESSION_PREFIX = "openai:conversation:whatsapp:"
GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")


class WhatsAppRelay:
    def __init__(self, agent_id, chat):
        self.agent_id = agent_id
        self.chat = chat

    def send(self, reply, context=None):
        text = str(getattr(reply, "content", "") or "").strip()
        if not text:
            return
        token = str(conf().get("external_api_token") or "")
        request = urllib.request.Request(
            f"{GATEWAY_URL}/whatsapp/internal/send",
            data=json.dumps({"agentId": self.agent_id, "chat": self.chat, "text": text}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        # Raising makes the scheduler keep the task and retry on its next tick
        # (e.g. while the phone is offline or the number is relinking).
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"WhatsApp gateway answered {response.status}")
        logger.info(f"[WhatsAppRelay] delivered scheduled message to {self.agent_id}/...{self.chat[-8:]}")


def relay_for(receiver):
    """The relay for a WhatsApp conversation's session id, else None."""
    receiver = str(receiver or "")
    if not receiver.startswith(SESSION_PREFIX):
        return None
    agent_id, _, chat = receiver[len(SESSION_PREFIX):].partition(":")
    if not agent_id or not chat:
        return None
    return WhatsAppRelay(agent_id, chat)
