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

    def post(self, text):
        """(ok, error) for one message, without raising."""
        try:
            self._post(text)
            return True, ""
        except urllib.error.HTTPError as e:
            try:
                why = json.loads(e.read() or b"{}").get("error") or ""
            except Exception:
                why = ""
            self.refused = e.code in (400, 403)  # not allowed: retrying can't help
            return False, (why or f"gateway answered {e.code}")[:160]
        except Exception as e:
            self.refused = False
            return False, str(e)[:160]

    def send(self, reply, context=None):
        text = str(getattr(reply, "content", "") or "").strip()
        if not text:
            return
        self._post(text, swallow_refusal=True)

    def _post(self, text, swallow_refusal=False):
        token = str(conf().get("external_api_token") or "")
        request = urllib.request.Request(
            f"{GATEWAY_URL}/whatsapp/internal/send",
            data=json.dumps({"agentId": self.agent_id, "chat": self.chat, "text": text}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        # Raising keeps the task for the next tick (phone offline); a 400/403 refusal is final.
        # The gateway de-duplicates, so a retry never sends twice.
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f"WhatsApp gateway answered {response.status}")
        except urllib.error.HTTPError as e:
            if swallow_refusal and e.code in (400, 403):
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


# WhatsApp numbers as delivery channels in the Tasks form (instances "lrm-wa:<agent>"); delivery
# goes through the relay above, so the gateway's rules still apply.

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
    for group in state.get("groups") or []:
        agent = group.get("agent")
        if not agent or not any(a.get("agent") == agent and a.get("status") != "unlinked" for a in state.get("accounts") or []):
            continue
        receiver = f"{SESSION_PREFIX}{agent}:{group['jid']}"
        out.append({"channel_type": "openai_api", "instance_id": INSTANCE_PREFIX + agent, "receiver": receiver,
                    "name": f"Group: {group.get('name') or group['jid']}", "is_group": True, "session_id": receiver,
                    "hint": "WhatsApp group", "instance_name": "WhatsApp"})
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


# Several recipients for one scheduled task
# The agent works once; the result goes to each recipient (the owner, designated contacts,
# ticked groups) through the same WhatsApp number. action["receivers"] lists them all.

def clean_receivers(instance_id, primary, extras):
    """The full, checked list of receivers, or ValueError naming what is not allowed."""
    wanted = list(dict.fromkeys([r for r in [primary, *(extras or [])] if r]))
    if len(wanted) <= 1:
        return wanted
    if not str(instance_id or "").startswith(INSTANCE_PREFIX):
        raise ValueError("several recipients work on the WhatsApp channels only")
    allowed = {r["receiver"]: r for r in task_recipients() if r["instance_id"] == instance_id}
    bad = [r for r in wanted if r not in allowed]
    if bad:
        raise ValueError("not allowed as a recipient (only the owner, designated contacts and ticked groups): "
                         + ", ".join(b.rsplit(":", 1)[-1] for b in bad))
    return wanted


class FanOut:
    """Sends one result to every receiver of the task and notes how each went."""

    def __init__(self, channel, action):
        self.channel = channel
        self.action = action
        self.results = []
        self.delivery_note = ""

    def send(self, reply, context=None):
        text = str(getattr(reply, "content", "") or "").strip()
        if not text:
            return
        names = {r["receiver"]: r["name"] for r in task_recipients()}
        transient = False
        for receiver in self.action.get("receivers") or []:
            relay = relay_for(receiver)
            if relay is None:
                self.results.append((receiver, False, "not a WhatsApp recipient"))
                continue
            ok, why = relay.post(text)
            self.results.append((names.get(receiver, receiver.rsplit(":", 1)[-1]), ok, why))
            if not ok and not getattr(relay, "refused", False):
                transient = True
        self.delivery_note = "\n\nDelivered: " + ", ".join(f"{n} {'✓' if ok else '✗ (' + why + ')'}" for n, ok, why in self.results)
        logger.info(f"[WhatsAppRelay] fan-out {self.delivery_note.strip()}")
        # Nobody got it: retry only when a failure may pass (offline), not when every one was refused.
        if self.results and not any(ok for _, ok, _ in self.results) and transient:
            raise RuntimeError("no recipient got it: " + "; ".join(f"{n}: {why}" for n, _, why in self.results))


def fanout(channel, action, channel_type):
    """The task's delivery: to every receiver when it has several, else the usual channel."""
    if channel_type == "openai_api" and len((action or {}).get("receivers") or []) > 1:
        return FanOut(channel, action)
    return channel
