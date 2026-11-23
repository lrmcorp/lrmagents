"""Deliver scheduled messages back to the WhatsApp chat they were set up in.

WhatsApp chats reach LRMagent through its OpenAI-compatible API (channel type
``openai_api``) with conversation ids ``whatsapp:<agent>:<chat jid>``, so the
scheduler has no channel to send through. This relay is that channel: it posts
to the WhatsApp gateway's internal send endpoint over the private Docker
network. Installed as channel/lrm_whatsapp_relay.py by the LRMagent image.
"""

import json
import os
import re
import urllib.error
import urllib.request

from common.log import logger
from config import conf

SESSION_PREFIX = "openai:conversation:whatsapp:"
GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")


# Not after a word character or dot (budi@62812…, order@2026…), and never a local 0… number.
_AT_NUMBER = re.compile(r"(?<![\w.])@(\+?[1-9]\d{7,14})\b")


def _task_mentions(action):
    """The E.164 numbers to tag for this task (group recipients only; the gateway ignores them
    for a personal chat)."""
    return [str(n).strip() for n in (action or {}).get("mentions") or [] if str(n).strip()]


def _mentions_for(action, text):
    """The numbers to tag: the task's own list, plus (only when the task opts in) any @number
    the agent wrote in its reply. Normalised to +digits and de-duplicated. Default: list only,
    so customer numbers in a report are never tagged by accident."""
    nums = list(_task_mentions(action))
    if (action or {}).get("mention_from_text"):
        nums += [m.group(1) for m in _AT_NUMBER.finditer(text or "")]
    seen, out = set(), []
    for n in nums:
        d = re.sub(r"\D", "", str(n))
        if len(d) >= 6 and d not in seen:
            seen.add(d)
            out.append("+" + d)
    return out


def _mention_note(result):
    """A short History note from the gateway's reply: who was tagged, who was skipped and why,
    and the message id. Empty when no mentions were involved, so ordinary deliveries read as before."""
    if not isinstance(result, dict):
        return ""
    bits = []
    if result.get("mentioned"):
        bits.append("tagged " + ", ".join("+" + str(n) for n in result["mentioned"]))
    for s in result.get("skipped") or []:
        bits.append(f"skipped +{s.get('number')} ({s.get('reason')})")
    if not bits:
        return ""
    if result.get("messageId"):
        bits.append("msg " + str(result["messageId"]))
    return " [" + "; ".join(bits) + "]"


class WhatsAppRelay:
    def __init__(self, agent_id, chat):
        self.agent_id = agent_id
        self.chat = chat
        self.action = {}            # the task's action, set for a single-recipient task (see fanout)
        self.last_result = {}       # the gateway's reply to the last send
        self.delivery_note = ""     # appended to the task's History preview
        self.refused = False        # the last post() was a final 400/403 refusal

    def post(self, text, mentions=None):
        """(ok, error) for one message, without raising."""
        try:
            self._post(text, mentions=mentions)
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
        self.last_result = {}
        refused = self._post(text, swallow_refusal=True, mentions=_mentions_for(self.action, text))
        # A refusal is final (no retry), so History must say it was not delivered.
        self.delivery_note = (f"\n\nNot delivered: the WhatsApp gateway refused it ({refused})" if refused
                              else _mention_note(self.last_result))

    def _post(self, text, swallow_refusal=False, mentions=None):
        """Raises on failure; with swallow_refusal a 400/403 returns its reason instead."""
        token = str(conf().get("external_api_token") or "")
        payload = {"agentId": self.agent_id, "chat": self.chat, "text": text}
        if mentions:
            payload["mentions"] = [str(n) for n in mentions]
        request = urllib.request.Request(
            f"{GATEWAY_URL}/whatsapp/internal/send",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        # Raising keeps the task for the next tick (phone offline); a 400/403 refusal is final.
        # The gateway de-duplicates, so a retry never sends twice.
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f"WhatsApp gateway answered {response.status}")
                try:
                    self.last_result = json.loads(response.read() or b"{}")
                except Exception:
                    self.last_result = {}
        except urllib.error.HTTPError as e:
            if swallow_refusal and e.code in (400, 403):
                body = e.read()[:200]
                logger.warning(f"[WhatsAppRelay] gateway refused {self.agent_id}/...{self.chat[-8:]}: {body!r}")
                try:
                    why = json.loads(body or b"{}").get("error") or ""
                except Exception:
                    why = ""
                return (why or f"gateway answered {e.code}")[:160]
            raise
        logger.info(f"[WhatsAppRelay] delivered scheduled message to {self.agent_id}/...{self.chat[-8:]}")
        return ""


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

    def targets(self):
        """Every receiver of the task, primary first, de-duplicated. The primary
        is always included so a task that saved only extra receivers still
        reaches the person it was addressed to."""
        primary = self.action.get("receiver")
        return list(dict.fromkeys(r for r in [primary, *(self.action.get("receivers") or [])] if r))

    def send(self, reply, context=None):
        text = str(getattr(reply, "content", "") or "").strip()
        if not text:
            return
        names = {r["receiver"]: r["name"] for r in task_recipients()}
        mentions = _mentions_for(self.action, text)
        transient = False
        for receiver in self.targets():
            relay = relay_for(receiver)
            if relay is None:
                self.results.append((receiver, False, "not a WhatsApp recipient", ""))
                continue
            ok, why = relay.post(text, mentions=mentions)
            self.results.append((names.get(receiver, receiver.rsplit(":", 1)[-1]), ok, why, _mention_note(relay.last_result) if ok else ""))
            if not ok and not getattr(relay, "refused", False):
                transient = True
        self.delivery_note = "\n\nDelivered: " + ", ".join(
            f"{n} {'✓' + note if ok else '✗ (' + why + ')'}" for n, ok, why, note in self.results)
        delivered = any(ok for _, ok, _, _ in self.results)
        if transient and delivered:
            # Raising would make the scheduler re-run the whole agent task (it does not just
            # re-deliver), so the others would get a second, different report: say so instead.
            missed = ", ".join(n for n, ok, _, _ in self.results if not ok)
            self.delivery_note += f"\nNot delivered to {missed}, and not retried; send it to them again if needed."
            logger.warning(f"[WhatsAppRelay] fan-out partly failed, not retried: {self.delivery_note.strip()}")
        logger.info(f"[WhatsAppRelay] fan-out {self.delivery_note.strip()}")
        # Nobody got it: retry only when a failure may pass (offline), not when every one was refused.
        if self.results and not delivered and transient:
            raise RuntimeError("no recipient got it: " + "; ".join(f"{n}: {why}" for n, _, why, _ in self.results))


def fanout(channel, action, channel_type):
    """The task's delivery: to every receiver when it has several, else the usual channel."""
    if channel_type != "openai_api":
        return channel
    fan = FanOut(channel, action)
    if len(fan.targets()) > 1:
        return fan
    # One recipient: the raw relay delivers, so hand it the task's action (mentions and flags).
    if isinstance(channel, WhatsAppRelay):
        channel.action = action or {}
    return channel
