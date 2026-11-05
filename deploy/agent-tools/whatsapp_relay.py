"""Deliver scheduled messages back to the WhatsApp chat they were set up in.

WhatsApp chats reach LRMagent through its OpenAI-compatible API (channel type
``openai_api``) with conversation ids ``whatsapp:<agent>:<chat jid>``, so the
scheduler has no channel to send through. This relay is that channel: it posts
to the WhatsApp gateway's internal send endpoint over the private Docker
network. Installed as channel/lrm_whatsapp_relay.py by the LRMagent image.
"""

import json
import os
import urllib.error
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
        # (e.g. while the phone is offline or the number is relinking). A refusal
        # (400/403: not a chat this agent may message) is final: retrying can't help.
        # The gateway de-duplicates, so a retry after a timeout never sends twice.
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f"WhatsApp gateway answered {response.status}")
        except urllib.error.HTTPError as e:
            if e.code in (400, 403):
                logger.warning(f"[WhatsAppRelay] gateway refused {self.agent_id}/...{self.chat[-8:]}: {e.read()[:200]!r}")
                return
            raise
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


# ---- WhatsApp as a delivery channel in the console's Tasks form ---------------------
# Upstream's task form only lists its own IM integrations. LRMagent's WhatsApp numbers
# live in the gateway, so each linked number is offered as an instance ("lrm-wa:<agent>")
# whose recipients are the owner and the designated contacts. Delivery goes through the
# relay above, so the gateway's rules (owner and designated contacts only) still apply.

INSTANCE_PREFIX = "lrm-wa:"


def _gateway_status():
    token = str(conf().get("external_api_token") or "")
    request = urllib.request.Request(f"{GATEWAY_URL}/whatsapp/internal/status", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read() or b"{}")
    except Exception as e:
        logger.debug(f"[WhatsAppRelay] gateway status unavailable: {e}")
        return {}


def _agent_names():
    try:
        from agent.registry import get_agent_registry
        return {p.id: p.name for p in get_agent_registry().list()}
    except Exception:
        return {}


def task_recipients(state=None):
    state = state if state is not None else _gateway_status()
    out = []
    for account in state.get("accounts") or []:
        agent = account.get("agent")
        if not agent or account.get("status") == "unlinked":
            continue
        instance = INSTANCE_PREFIX + agent
        owner = (account.get("ownerNumbers") or [account.get("phone")])[0]
        people = ([{"name": "You (owner)", "phone": owner}] if owner else []) + list(state.get("designated") or [])
        for person in people:
            phone = str(person.get("phone") or "")
            if not phone:
                continue
            receiver = f"{SESSION_PREFIX}{agent}:{phone}@s.whatsapp.net"
            out.append({"channel_type": "openai_api", "instance_id": instance, "receiver": receiver,
                        "name": person.get("name") or f"+{phone}", "is_group": False, "session_id": receiver,
                        "hint": f"+{phone}", "instance_name": "WhatsApp"})
    return out


def task_instances(state=None):
    state = state if state is not None else _gateway_status()
    names = _agent_names()
    recipients = task_recipients(state)
    out = []
    for account in state.get("accounts") or []:
        agent = account.get("agent")
        if not agent or account.get("status") == "unlinked":
            continue
        instance = INSTANCE_PREFIX + agent
        out.append({"instance_id": instance, "channel_type": "openai_api",
                    "name": f"{names.get(agent, agent)}'s WhatsApp" + (f" (+{account.get('phone')})" if account.get("phone") else ""),
                    "channel_label": "WhatsApp", "agent_id": agent,
                    "recipient_count": sum(1 for r in recipients if r["instance_id"] == instance)})
    return out


def task_recipient(instance_id, receiver):
    if not str(instance_id or "").startswith(INSTANCE_PREFIX):
        return None
    return next((r for r in task_recipients() if r["instance_id"] == instance_id and r["receiver"] == receiver), None)


def instance_agent(instance_id):
    instance_id = str(instance_id or "")
    return instance_id[len(INSTANCE_PREFIX):] if instance_id.startswith(INSTANCE_PREFIX) else ""
