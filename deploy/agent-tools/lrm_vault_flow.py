"""Vault updates: every agent's final results reach the vault through Lorium and the owner.

  agent  --vault_update propose-->  review (Lorium checks, corrects, merges with what the vault has)
  Lorium --vault_update review--->  owner  (WhatsApp notice; Vault page → Updates: approve, edit, reject)
  owner  --approve--------------->  applied: the note is created or brought up to date, dated,
                                    with a change-log line; Git sync keeps the old version.

Agents can't write knowledge/ directly any more (write/edit and shell writes there are refused),
so the vault only ever holds what the owner approved. Lorium is the default agent; what Lorium
proposes itself goes straight to the owner. Installed as agent/lrm_vault_flow.py; the console
endpoint (/api/lrm/vault-updates) is channel/web/api/lrm_vault_updates.py.
"""
import datetime
import difflib
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
    p["exists"] = current_text(p["path"]) is not None
    p["diff"] = diff(current_text(p["path"]), p["content"]) if p["exists"] else ""
    p["agent_name"] = agent_name(p["agent"])
    return p


# ---- steps -----------------------------------------------------------------------------

def propose(agent_id, session_id, title, content, summary, folder="", path="", sources=None):
    content = str(content or "").strip()
    if not content:
        raise ValueError("the note needs content")
    if len(content) > MAX_CONTENT:
        raise ValueError("the note is too long")
    vpath = note_path(title, folder, path)
    _vault().resolve(vpath)  # refuses paths outside the vault
    lorium = reviewer_id()
    item = {"id": secrets.token_hex(4), "status": "owner" if agent_id == lorium else "review", "path": vpath,
            "title": title or Path(vpath).stem, "content": content, "summary": str(summary or "")[:600],
            "sources": [str(s)[:300] for s in (sources or [])][:20], "agent": agent_id, "session": session_id,
            "review_note": "", "created": time.time(), "updated": time.time(), "history": [f"proposed by {agent_name(agent_id)}"]}
    with _lock:
        items = _load()
        # a newer proposal for the same note replaces one still waiting
        for old in items:
            if old["path"] == vpath and old["status"] in ("review", "owner"):
                old["status"] = "replaced"
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
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None or item["status"] != "review":
            raise ValueError("no such update waiting for review")
        if decision == "reject":
            item["status"] = "rejected"
        else:
            if content and str(content).strip():
                item["content"] = str(content).strip()[:MAX_CONTENT]
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


def _apply(item):
    """Write the approved note: dated, with a change-log line; the vault index is refreshed."""
    vault = _vault()
    section, rel, full = vault.resolve(item["path"])
    text = item["content"].rstrip() + "\n"
    today = datetime.date.today()
    stamp = today.strftime("%d-%m-%Y")
    line = f"- {stamp}: {item['summary'] or 'updated'} ({agent_name(item['agent'])}, checked by {agent_name(reviewer_id())}, approved by the owner)"
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


def pending(status=("review", "owner")):
    return [_view(i) for i in reversed(_load()) if i["status"] in status]


def recent(n=30):
    return [_view(i) for i in list(reversed(_load()))[:n]]


# ---- the people in the loop ------------------------------------------------------------

def _token_port():
    from config import conf
    return str(conf().get("external_api_token") or ""), int(conf().get("web_port") or os.environ.get("WEB_PORT") or 9899)


def _ask_lorium(item):
    token, port = _token_port()
    lorium = reviewer_id()
    text = (f"[Vault review] {agent_name(item['agent'])} proposes a vault update (id {item['id']}) for {item['path']}"
            f"{' (the note exists: bring it up to date)' if current_text(item['path']) is not None else ' (new note)'}.\n"
            f"Summary: {item['summary'] or '-'}\nSources: {', '.join(item['sources']) or '-'}\n\n--- proposed note ---\n{item['content'][:12000]}\n--- end ---\n\n"
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
        + ["", "Reply *yes* to save or *no* to reject."
           + (f" Full note: {domain} → Vault → Updates." if domain else " Full note: Vault → Updates.")])
    try:
        req = urllib.request.Request(f"{url}/whatsapp/internal/notify-owner", method="POST",
                                     data=json.dumps({"agentId": reviewer_id(), "text": text}).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        logger.info(f"[VaultFlow] owner not notified on WhatsApp: {e}")


# ---- only the flow writes knowledge/ ----------------------------------------------------

_WRITE_TOOLS = ("write", "edit")
_SHELL_WRITE = re.compile(r"(>|\btee\b|\bmv\b|\bcp\b|\brm\b|\bsed\s+-i|\btruncate\b|\bmkdir\b|\btouch\b|\bln\b|\brsync\b|\bgit\b)")


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
        if ("knowledge/" in cmd or any(r in cmd for r in roots)) and _SHELL_WRITE.search(cmd):
            return msg
    return None
