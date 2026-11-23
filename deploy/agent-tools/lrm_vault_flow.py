"""Vault updates: every agent's final results reach the vault through Lorium and the owner.

  agent  --vault_update propose-->  review (Lorium checks, corrects, merges with what the vault has)
  Lorium --vault_update review--->  owner  (WhatsApp notice; Vault page → Updates: approve, edit, reject)
  owner  --approve--------------->  applied: the note is created or brought up to date, dated,
                                    with a change-log line; Git sync keeps the old version.

Agents can't write knowledge/ directly any more (write/edit and shell writes there are refused),
so the vault only ever holds what the owner approved, with three refinements:
- Secrets (passwords, PINs, keys, card and ID numbers) are refused: agents can read the vault, so
  they belong in the owner's Private vault.
- Two proposals for the same note are merged by Lorium into one, never sent to the owner twice.
- A small correction to an existing note that Lorium checked is saved at once (it can be undone),
  and listed in a daily digest; new notes and big changes still wait for the owner's yes or no.
  Waiting updates get one reminder after 3 days and expire after 14. Lorium is the default agent; what Lorium
proposes itself goes straight to the owner. Installed as agent/lrm_vault_flow.py; the console
endpoint (/api/lrm/vault-updates) is channel/web/api/lrm_vault_updates.py.
"""
import datetime
import difflib
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.request
from pathlib import Path

from common.log import logger

_lock = threading.Lock()
MAX_CONTENT = 200_000
DAY = 86400
REMIND_AFTER = 3 * DAY
EXPIRE_AFTER = 14 * DAY
DIGEST_HOUR = 20  # the daily list of small corrections saved on their own
SMALL_RATIO = 0.9  # an edit keeping this much of the note (and few lines changed) counts as small
SMALL_LINES = 12

# Secrets never go into the shared vault
_SECRET_PATTERNS = (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    (r"\b(sk-[A-Za-z0-9_-]{16,}|sk-or-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b", "an API key or token"),
    (r"(?i)\b(seed|recovery|mnemonic) (phrase|words)\b", "a wallet recovery phrase"),
)


def _luhn(number):
    digits = [int(c) for c in number][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return total % 10 == 0


# A label only counts when a secret-looking value follows it (see _secret_value).
_LABEL = re.compile(r"(?i)\b(password|passwd|pwd|passcode|kata ?sandi|sandi|secret|pin|otp|cvv|cvc|api[ _-]?key|token)\b"
                    r"\s*[:=]\s*[`'\"]?([^\s`'\"]+)[`'\"]?([^\n]*)")
_PW_LABELS = ("password", "passwd", "pwd", "passcode", "sandi", "secret")
_CARD_WORDS = re.compile(r"(?i)\b(kartu|card|kredit|credit|debit|visa|master ?card|amex|jcb|cc)\b")
_CARD_PREFIX = re.compile(r"^(4|5[1-5]|2[2-7]|3[47]|35|6011|65)")


def _secret_value(label, value, rest):
    """Is what follows a password/PIN/token label a secret, not ordinary words or an amount?"""
    label = label.lower()
    more_words = bool(re.match(r"[ \t]+[^\W\d_]", rest))  # "lokasi gudang", "500 kWh"
    if re.fullmatch(r"\d+([.,]\d+)*[A-Za-z]{0,4}", value) and (more_words or re.search(r"[A-Za-z]", value)):
        return False  # a number with a unit
    letters, digits = bool(re.search(r"[A-Za-z]", value)), bool(re.search(r"\d", value))
    mixed = letters and (digits or bool(re.search(r"[^\w]", value)))
    if label in ("pin", "otp", "cvv", "cvc"):
        return (value.isdigit() and 3 <= len(value) <= 8) or (mixed and len(value) >= 4 and not more_words)
    if label.endswith(_PW_LABELS):
        return len(value) >= 3 and (mixed or not more_words)
    return len(value) >= 8 and (mixed or len(value) >= 16) and not more_words  # token / api key


def _card_number(raw, before):
    """Is this Luhn-valid digit run a card number? Phone-shaped numbers (62…, +62…, 08…) and
    other long ids need card context: 4-4-4-4 grouping or a card word just before."""
    digits = re.sub(r"\D", "", raw)
    if not (13 <= len(digits) <= 19 and _luhn(digits)):
        return False
    grouped = bool(re.fullmatch(r"\d{4}([ -])\d{4}\1\d{4}\1\d{4}(\1\d{1,4})?|\d{4}([ -])\d{6}\3\d{5}", raw.strip()))
    if grouped or _CARD_WORDS.search(before[-40:]):
        return True
    phone = before.rstrip().endswith("+") or digits.startswith(("62", "08"))
    return not phone and len(digits) in (15, 16) and bool(_CARD_PREFIX.match(digits))


def secret_in(text):
    """What kind of secret the text holds, or None."""
    text = str(text or "")
    for pattern, kind in _SECRET_PATTERNS:
        if re.search(pattern, text):
            return kind
    for m in _LABEL.finditer(text):
        if _secret_value(m.group(1), m.group(2), m.group(3)):
            return "a password, PIN or key"
    for m in re.finditer(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text):
        if _card_number(m.group(0), text[:m.start()]):
            return "a card number"
    if re.search(r"(?i)\b(nik|ktp|no\.? ?ktp|nomor induk)\b\D{0,12}\d{16}\b", text):
        return "an ID card number (NIK)"
    return None


SECRET_REFUSAL = ("This note holds {kind}. Secrets never go into the vault, because every agent can read it: the owner keeps "
                  "them in their Private vault (Knowledge → Private vault), which no agent can open. Remove it (for example "
                  "write 'kept in the owner's Private vault') and propose the note again.")


def _check_secrets(content):
    kind = secret_in(content)
    if kind:
        raise ValueError(SECRET_REFUSAL.format(kind=kind))


def small_change(old, new):
    """A correction (most of the note kept, a few lines changed), not a new note or a rewrite."""
    if old is None:
        return False
    a, b = old.splitlines(), new.splitlines()
    changed = sum(1 for l in difflib.ndiff(a, b) if l[:1] in "+-")
    return difflib.SequenceMatcher(None, a, b).ratio() >= SMALL_RATIO and changed <= SMALL_LINES


def _file():
    from config import get_data_root
    return Path(get_data_root()) / "lrm_vault_updates.json"


def _load():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items):
    path = _file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def reviewer_id():
    from agent.registry import get_agent_registry
    reg = get_agent_registry()
    return reg.default_agent_id if hasattr(reg, "default_agent_id") else reg.get(require_enabled=False).id


def agent_name(agent_id):
    try:
        from agent.registry import get_agent_registry
        return get_agent_registry().get(agent_id, require_enabled=False).name
    except Exception:
        return agent_id or "agent"


def _vault():
    from channel.web.api.lrm_vault import Vault
    return Vault(reviewer_id())


def note_path(title="", folder="", path=""):
    """knowledge/<folder>/<title>.md (or the given knowledge/ path)."""
    if path:
        p = path.strip().strip("/")
        if not p.startswith("knowledge/"):
            p = f"knowledge/{p}"
        return p if p.lower().endswith(".md") else p + ".md"
    name = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', " ", title or "").strip(" .")[:100] or "Untitled"
    folder = re.sub(r"[^\w .()/-]", " ", folder or "").strip(" /")
    return "/".join(x for x in ("knowledge", folder, f"{name}.md") if x)


def current_text(vpath):
    try:
        _, _, full = _vault().resolve(vpath)
        return full.read_text(encoding="utf-8") if full.exists() else None
    except Exception:
        return None


def diff(old, new):
    if old is None:
        return ""
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), "now", "after", lineterm="", n=2)
    return "\n".join(list(lines)[2:])[:20000]


def _view(p):
    p = dict(p)
    p.pop("previous", None)  # the text before an automatic correction: only kept for Undo
    p.pop("written_sha", None)
    p["exists"] = current_text(p["path"]) is not None
    p["diff"] = diff(current_text(p["path"]), p["content"]) if p["exists"] else ""
    p["agent_name"] = agent_name(p["agent"])
    return p


# Steps

def propose(agent_id, session_id, title, content, summary, folder="", path="", sources=None):
    content = str(content or "").strip()
    if not content:
        raise ValueError("the note needs content")
    if len(content) > MAX_CONTENT:
        raise ValueError("the note is too long")
    _check_secrets(content)
    vpath = note_path(title, folder, path)
    _vault().resolve(vpath)  # refuses paths outside the vault
    lorium = reviewer_id()
    item = {"id": secrets.token_hex(4), "status": "owner" if agent_id == lorium else "review", "path": vpath,
            "title": title or Path(vpath).stem, "content": content, "summary": str(summary or "")[:600],
            "sources": [str(s)[:300] for s in (sources or [])][:20], "agent": agent_id, "session": session_id,
            "review_note": "", "created": time.time(), "updated": time.time(), "history": [f"proposed by {agent_name(agent_id)}"]}
    with _lock:
        items = _load()
        # Another proposal for the same note still waiting: Lorium merges them into one.
        others = [o for o in items if o["path"] == vpath and o["status"] in ("review", "owner")]
        if others:
            item["others"] = [{"agent": agent_name(o["agent"]), "summary": o["summary"], "content": o["content"][:20000]} for o in others]
            item["status"] = "review"
            item["history"].append("to be merged with " + ", ".join(f"{agent_name(o['agent'])}'s version" for o in others))
            for o in others:
                o["status"] = "merged"
                o["history"].append(f"merged into {item['id']}")
        if item["status"] == "owner":
            item["code"] = _new_code(items)
        items.append(item)
        _save(items)
    if item["status"] == "review":
        threading.Thread(target=_ask_lorium, args=(item,), daemon=True).start()
    else:
        threading.Thread(target=_notify_owner, args=(item,), daemon=True).start()
    return item


def _new_code(items):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    taken = {i.get("code") for i in items if i["status"] == "owner"}
    try:
        from agent import lrm_activity
        taken |= {r["code"] for r in lrm_activity.pending()}
    except Exception:
        pass
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        if code not in taken:
            return code


def decide_by_code(code, decision):
    """A WhatsApp reply (YES/NO CODE) to an update waiting for the owner; None when no such code."""
    code = str(code or "").strip().upper()
    item = next((i for i in _load() if i["status"] == "owner" and i.get("code") == code), None)
    if item is None:
        return None
    return owner_decide(item["id"], "approve" if decision in ("approve", "approved", "yes", True) else "reject")


def review(item_id, by_agent, decision, content=None, note=""):
    if by_agent != reviewer_id():
        raise PermissionError(f"Only {agent_name(reviewer_id())} reviews vault updates.")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None or item["status"] != "review":
            raise ValueError("no such update waiting for review")
        if decision == "reject":
            item["status"] = "rejected"
        else:
            if content and str(content).strip():
                _check_secrets(content)
                item["content"] = str(content).strip()[:MAX_CONTENT]
            old = current_text(item["path"])
            if small_change(old, item["content"]) and not item.get("others"):
                # A checked correction: saved now, listed in tonight's digest, can be undone.
                item["previous"] = old
                item["review_note"] = str(note or "")[:1000]
                item["history"].append(f"approved by {agent_name(by_agent)}" + (f": {note}" if note else ""))
                # What Undo may replace: if the note no longer holds exactly this, Undo is refused.
                item["written_sha"] = _sha(_apply(item, approved_by="auto"))
                item["status"] = "applied"
                item["auto"] = True
                item["history"].append("saved on its own (a small correction); in the daily digest")
                item["updated"] = time.time()
                _save(items)
                return item
            item["status"] = "owner"
            item["code"] = _new_code(items)
        item["review_note"] = str(note or "")[:1000]
        item["updated"] = time.time()
        item["history"].append(f"{decision} by {agent_name(by_agent)}" + (f": {note}" if note else ""))
        _save(items)
    if item["status"] == "owner":
        threading.Thread(target=_notify_owner, args=(item,), daemon=True).start()
    return item


def owner_decide(item_id, decision, content=None):
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None or item["status"] != "owner":
            raise ValueError("no such update waiting for you")
        if decision != "approve":
            item["status"] = "rejected"
            item["history"].append("rejected by the owner")
            item["updated"] = time.time()
            _save(items)
            return item
        if content and str(content).strip():
            item["content"] = str(content).strip()[:MAX_CONTENT]
        _apply(item)
        item["status"] = "applied"
        item["history"].append("approved by the owner")
        item["updated"] = time.time()
        _save(items)
    return item


def revert(item_id):
    """Undo a small correction that was saved on its own: the note goes back to how it was."""
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None or not item.get("auto") or item["status"] != "applied" or item.get("previous") is None:
            raise ValueError("only a correction saved on its own can be undone")
        vault = _vault()
        section, rel, full = vault.resolve(item["path"])
        now = full.read_text(encoding="utf-8") if full.exists() else None
        later = any(i is not item and i["path"] == item["path"] and i["status"] == "applied"
                    and i.get("updated", 0) > item.get("updated", 0) for i in items)
        if later or (item.get("written_sha") and (now is None or _sha(now) != item["written_sha"])):
            raise ValueError("this correction can't be undone: the note has changed since it was saved (a later update "
                             "or edit), and undoing it would throw that change away. Edit the note instead.")
        from common.atomic_write import write_text_atomic
        write_text_atomic(full, item["previous"])
        try:
            vault.changed({section}, [rel])
        except Exception as e:
            logger.warning(f"[VaultFlow] index refresh after undo {rel}: {e}")
        item["status"] = "reverted"
        item["history"].append("undone by the owner")
        item["updated"] = time.time()
        _save(items)
    return item


def decide_all(decision):
    """The owner's "yes all" / "no all": every update waiting for them. Returns the items decided."""
    done = []
    for item in [i for i in _load() if i["status"] == "owner"]:
        try:
            done.append(owner_decide(item["id"], "approve" if decision in ("approve", "yes", True) else "reject"))
        except Exception as e:
            logger.warning(f"[VaultFlow] {decision} all: {item['id']}: {e}")
    return done


def _apply(item, approved_by="owner"):
    """Write the approved note: dated, with a change-log line; the vault index is refreshed."""
    vault = _vault()
    section, rel, full = vault.resolve(item["path"])
    text = item["content"].rstrip() + "\n"
    today = datetime.date.today()
    stamp = today.strftime("%d-%m-%Y")
    who = "a small correction, saved on its own" if approved_by == "auto" else "approved by the owner"
    line = f"- {stamp}: {item['summary'] or 'updated'} ({agent_name(item['agent'])}, checked by {agent_name(reviewer_id())}, {who})"
    old = full.read_text(encoding="utf-8") if full.exists() else ""
    # keep the earlier change log under the new text
    log = re.search(r"\n## Change log\n(.*)$", old, re.S)
    earlier = log.group(1).strip() if log else ""
    text = re.sub(r"\n## Change log\n.*$", "", text, flags=re.S).rstrip() + "\n"
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        head, body = (text[4:end], text[end + 4:]) if end > 0 else ("", text)
        head = re.sub(r"^updated:.*\n?", "", head, flags=re.M).rstrip()
        text = f"---\n{head}\nupdated: {today.isoformat()}\n---{body}"
    else:
        text = f"---\nupdated: {today.isoformat()}\n---\n\n{text}"
    text = text.rstrip() + f"\n\n## Change log\n{line}\n" + (earlier + "\n" if earlier else "")
    full.parent.mkdir(parents=True, exist_ok=True)
    from common.atomic_write import write_text_atomic
    write_text_atomic(full, text)
    try:
        vault.changed({section}, [rel])
    except Exception as e:
        logger.warning(f"[VaultFlow] index refresh after {rel}: {e}")
    logger.info(f"[VaultFlow] applied {item['path']}")
    return text


def _sha(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def pending(status=("review", "owner")):
    return [_view(i) for i in reversed(_load()) if i["status"] in status]


def recent(n=30):
    return [_view(i) for i in list(reversed(_load()))[:n]]


# The people in the loop

def _token_port():
    from config import conf
    return str(conf().get("external_api_token") or ""), int(conf().get("web_port") or os.environ.get("WEB_PORT") or 9899)


def _ask_lorium(item):
    token, port = _token_port()
    lorium = reviewer_id()
    others = "".join(f"\n--- another version, from {o['agent']} ({o['summary'] or 'no summary'}) ---\n{o['content'][:8000]}\n--- end ---\n"
                     for o in item.get("others") or [])
    text = (f"[Vault review] {agent_name(item['agent'])} proposes a vault update (id {item['id']}) for {item['path']}"
            f"{' (the note exists: bring it up to date)' if current_text(item['path']) is not None else ' (new note)'}.\n"
            f"Summary: {item['summary'] or '-'}\nSources: {', '.join(item['sources']) or '-'}\n\n--- proposed note ---\n{item['content'][:12000]}\n--- end ---\n"
            + (f"\nOther agents proposed changes to the same note at the same time. MERGE them all into one complete note "
               f"(keep every correct fact, the newest wins when they disagree) and approve it with that merged 'content'.\n{others}" if others else "")
            + "\nNever let passwords, PINs, keys, card or ID numbers into the vault: remove them (write 'kept in the owner's Private vault').\n\n"
            "Check it: is it correct, useful and not a duplicate? Compare with what the vault already has (memory_search, read "
            "the existing note) and keep the newest information. Then call vault_update with action 'review', this id, and "
            "decision 'approve' (optionally with corrected full 'content' and a short 'note' for the owner) or 'reject' with the reason.")
    body = {"agent_id": lorium, "conversation_id": f"lrm-review:{lorium}:{datetime.date.today().isoformat()}", "stream": False,
            "messages": [{"role": "user", "content": text}]}
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", method="POST", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=900).read()
    except Exception as e:
        logger.warning(f"[VaultFlow] Lorium could not review {item['id']}: {e}")


def _send_owner(text):
    token, _ = _token_port()
    url = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
    req = urllib.request.Request(f"{url}/whatsapp/internal/notify-owner", method="POST",
                                 data=json.dumps({"agentId": reviewer_id(), "text": text}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


# Reminders, expiry and the daily digest

def housekeeping(now=None, send=None):
    """Runs every half hour: one reminder for updates waiting 3 days, expiry after 14,
    and in the evening the list of small corrections saved on their own that day."""
    now = now or time.time()
    send = send or _send_owner
    remind, expired, digest = [], [], []
    with _lock:
        items = _load()
        for i in items:
            age = now - i.get("created", now)
            if i["status"] in ("review", "owner") and age >= EXPIRE_AFTER:
                i["status"] = "expired"
                i["history"].append("expired after 14 days without a decision")
                i["updated"] = now
                expired.append(i)
            elif i["status"] == "owner" and age >= REMIND_AFTER and not i.get("reminded"):
                i["reminded"] = now
                remind.append(i)
        hour = datetime.datetime.fromtimestamp(now).hour
        if hour >= DIGEST_HOUR:
            digest = [i for i in items if i.get("auto") and i["status"] == "applied" and not i.get("digested")]
            for i in digest:
                i["digested"] = now
        if remind or expired or digest:
            _save(items)
    short = lambda i: i["path"].replace("knowledge/", "", 1)
    try:
        if remind:
            send("\n".join([f"*Vault updates waiting · {len(remind)}*"]
                            + [f"- {short(i)}: {i['summary'] or 'update'} ({agent_name(i['agent'])}, {int((now - i['created']) // DAY)} days)" for i in remind]
                            + ["", "Reply *yes all* to save them all, *no all* to reject them, or decide each in Vault → Updates. They expire after 14 days."]))
        if expired:
            send("\n".join([f"*Vault updates expired · {len(expired)}*"] + [f"- {short(i)}: {i['summary'] or 'update'}" for i in expired]
                            + ["", "Nothing was saved. An agent can propose them again."]))
        if digest:
            send("\n".join([f"*Vault: small corrections saved today · {len(digest)}*"]
                            + [f"- {short(i)}: {i['summary'] or 'corrected'} ({agent_name(i['agent'])})" for i in digest]
                            + ["", "Checked by " + agent_name(reviewer_id()) + ". To undo one: Vault → Updates → Undo."]))
    except Exception as e:
        logger.info(f"[VaultFlow] housekeeping notice not sent: {e}")
    return {"remind": remind, "expired": expired, "digest": digest}


_started = False


def start_background():
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            time.sleep(1800)
            try:
                housekeeping()
            except Exception as e:
                logger.warning(f"[VaultFlow] housekeeping: {e}")
    threading.Thread(target=loop, name="lrm-vault-housekeeping", daemon=True).start()


def _notify_owner(item):
    token, _ = _token_port()
    url = os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/")
    domain = (os.environ.get("WEB_PUBLIC_URL") or "").rstrip("/")
    lines = [l.strip() for l in item["content"].splitlines() if l.strip() and not l.startswith("---")]
    preview = [l for l in lines if not l.startswith("#") and (l.startswith(("-", "*")) or len(l) < 140)][:8]
    checked = f", checked by {agent_name(reviewer_id())}" if item["agent"] != reviewer_id() else ""
    text = "\n".join(
        [f"*Vault update · {item['title']}*",
         f"- {'Updates' if current_text(item['path']) is not None else 'New'}: {item['path'].replace('knowledge/', '', 1)}",
         f"- From {agent_name(item['agent'])}{checked}"]
        + ([f"- What: {item['summary']}"] if item["summary"] else [])
        + ([f"- Note: {item['review_note']}"] if item.get("review_note") else [])
        + ["", *[(l if l.startswith(("-", "*")) else f"> {l}") for l in preview]]
        + ["", "Reply *yes* to save or *no* to reject (*yes all* / *no all* for everything waiting)."
           + (f" Full note: {domain} → Vault → Updates." if domain else " Full note: Vault → Updates.")])
    try:
        req = urllib.request.Request(f"{url}/whatsapp/internal/notify-owner", method="POST",
                                     data=json.dumps({"agentId": reviewer_id(), "text": text}).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        logger.info(f"[VaultFlow] owner not notified on WhatsApp: {e}")


# Only the flow writes knowledge/

_WRITE_TOOLS = ("write", "edit")
# Shell commands: reading knowledge/ is fine; only a write whose target is inside it is refused.
_WS = "/WS"  # the agent's working directory, where knowledge/ is relative to
_SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", "{", "}"}
_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}
_ANY_PATH = {"rm", "rmdir", "unlink", "shred", "truncate", "mkdir", "touch", "chmod", "chown", "mv"}
_DEST_PATH = {"cp", "ln", "rsync", "install"}
_SHELLS = {"sh", "bash", "zsh", "dash"}
_GIT_READ = {"status", "log", "diff", "show", "grep", "blame", "ls-files", "ls-tree", "rev-parse", "cat-file",
             "shortlog", "describe", "help", "--version"}
_CODE_WRITE = re.compile(
    r"""(,\s*(mode\s*=\s*)?|\.open\(\s*)['"](?=[rbt]*[wax+])[rbtwax+]{1,3}['"]|\.write_(text|bytes)\s*\(|"""
    r"""\b(remove|unlink|rename|replace|rmdir|makedirs|mkdir|truncate|rmtree|copy2?|copyfile|move|"""
    r"""writeFileSync|writeFile|appendFileSync|appendFile|unlinkSync|renameSync|rmSync|mkdirSync|copyFileSync|"""
    r"""file_put_contents|system|popen)\s*\(|subprocess|File\.write|open\s*\(?[^)]*['"]\s*\+?>""")


def _interpreter(word):
    return bool(re.fullmatch(r"(python[\d.]*|node|nodejs|perl|ruby|php|deno|bun)", word))


def _heredocs(cmd):
    """(command lines, {line index: heredoc body}) with heredoc bodies taken out."""
    lines, bodies, out, i = cmd.split("\n"), {}, [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        i += 1
        if m:
            body = []
            while i < len(lines) and lines[i].strip() != m.group(1):
                body.append(lines[i])
                i += 1
            i += 1
            bodies[len(out) - 1] = "\n".join(body)
    return out, bodies


def shell_writes_knowledge(cmd, roots, cwd=_WS):
    """True when a bash command writes, moves or deletes something inside knowledge/."""
    import shlex
    roots = [r.rstrip("/") for r in roots]

    def inside(path, here):
        if not path or path.startswith("-") or path.startswith("/dev/"):
            return False
        full = os.path.normpath(path if path.startswith("/") else os.path.join(here, path))
        return any(full == r or full.startswith(r + "/") for r in [_WS + "/knowledge", *roots])

    lines, bodies = _heredocs(str(cmd or ""))
    for n, line in enumerate(lines):
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            # unparseable (open quote): refuse a write-looking command that names the vault
            return bool(re.search(r"(>|\btee\b|\bmv\b|\bcp\b|\brm\b|\bsed\s+-i|\bgit\b)", line))
        segments, seg = [], []
        for t in tokens:
            if t in _SEPARATORS:
                segments.append(seg)
                seg = []
            else:
                seg.append(t)
        segments.append(seg)
        for seg in segments:
            words = []
            i = 0
            while i < len(seg):
                t = seg[i]
                if t in _REDIRECTS or re.fullmatch(r"\d?>>?", t):
                    target = seg[i + 1] if i + 1 < len(seg) else ""
                    if inside(target, cwd):
                        return True
                    i += 2
                    continue
                if t in ("<", "<<", "<<<", "<<-", ">&", "<&") or re.fullmatch(r"\d?[<>]&", t):
                    i += 2
                    continue
                if re.fullmatch(r"\d", t) and i + 1 < len(seg) and seg[i + 1] in _REDIRECTS | {">&"}:
                    i += 1  # the fd of "2>"
                    continue
                words.append(t)
                i += 1
            while words and (re.fullmatch(r"\w+=.*", words[0]) or words[0] in ("sudo", "env", "command", "nohup", "time", "xargs")):
                words = words[1:]
            if not words:
                continue
            name, args = os.path.basename(words[0]), words[1:]
            paths = [a for a in args if not a.startswith("-")]
            if name in ("cd", "pushd"):
                target = paths[0] if paths else "/HOME"
                cwd = os.path.normpath(target if target.startswith("/") else os.path.join(cwd, target))
            elif name in _ANY_PATH and any(inside(a, cwd) for a in paths):
                return True
            elif name in _DEST_PATH and paths and inside(paths[-1], cwd):
                return True
            elif name == "tee" and any(inside(a, cwd) for a in paths):
                return True
            elif name == "sed" and any(a.startswith("--in-place") or re.fullmatch(r"-[a-zA-Z]*i.*", a) for a in args) \
                    and any(inside(a, cwd) for a in paths[1:]):
                return True
            elif name == "dd" and any(a.startswith("of=") and inside(a[3:], cwd) for a in args):
                return True
            elif name == "find" and any(inside(a, cwd) for a in paths + ["."] * (not paths)) and (
                    "-delete" in args or any(a in ("-exec", "-execdir", "-ok") and j + 1 < len(args)
                                             and os.path.basename(args[j + 1]) in _ANY_PATH | _DEST_PATH | {"sed", "tee"}
                                             for j, a in enumerate(args))):
                return True
            elif name == "git":
                here, rest = cwd, list(args)
                while rest and rest[0].startswith("-"):
                    opt = rest.pop(0)
                    if opt == "-C" and rest:
                        d = rest.pop(0)
                        here = os.path.normpath(d if d.startswith("/") else os.path.join(here, d))
                    elif opt in ("-c", "--git-dir", "--work-tree") and rest:
                        rest.pop(0)
                sub = rest[0] if rest else ""
                if sub and sub not in _GIT_READ and (inside(here, here) or any(inside(a, here) for a in rest[1:])):
                    return True
            elif name in _SHELLS:
                code = args[args.index("-c") + 1] if "-c" in args and args.index("-c") + 1 < len(args) else bodies.get(n)
                if code and shell_writes_knowledge(code, roots, cwd):
                    return True
            elif _interpreter(name):
                flag = next((f for f in ("-c", "-e", "-r", "--eval") if f in args), None)
                code = args[args.index(flag) + 1] if flag and args.index(flag) + 1 < len(args) else bodies.get(n, "")
                names_vault = "knowledge" in code or any(r in code for r in roots) or inside(".", cwd)
                if names_vault and _CODE_WRITE.search(code):
                    return True
    return False


def knowledge_roots():
    roots = set()
    try:
        from agent.registry import get_agent_registry
        from common import state_dir
        for p in get_agent_registry().list():
            roots.add(os.path.realpath(state_dir.knowledge_dir(base=str(p.workspace))))
    except Exception:
        pass
    return roots


def refusal(executor, tool_name, arguments):
    args = arguments or {}
    roots = knowledge_roots()
    if not roots:
        return None
    msg = ("The vault only changes with the owner's approval: use the vault_update tool (action 'propose') with the full note; "
           "Lorium checks it and the owner approves it.")
    if tool_name in _WRITE_TOOLS:
        raw = str(args.get("path") or args.get("file_path") or "")
        if not raw:
            return None
        try:
            workspace = os.path.realpath(executor.agent.effective_cwd())
        except Exception:
            workspace = "/"
        full = os.path.realpath(os.path.join(workspace, os.path.expanduser(raw)))
        if any(full == r or full.startswith(r + os.sep) for r in roots):
            return msg
    if tool_name == "bash":
        cmd = str(args.get("command") or "")
        if ("knowledge" in cmd or any(r in cmd for r in roots)) and shell_writes_knowledge(cmd, roots):
            return msg
    return None
