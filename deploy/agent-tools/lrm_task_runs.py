"""The scheduled run in progress, per session: its task, run id and recipients.

A scheduled task's final answer is delivered by the scheduler to the task's recipients, so a
whatsapp send to one of them during the run would send twice and ask the owner for an
approval nobody needs: lrm_activity.gate refuses it (no approval) with scheduled_duplicate().
Every tool call and approval of the run is logged with its run id (Activity page).
Installed as agent/lrm_task_runs.py; scheduler/integration.py calls start() and finish().
"""
import re
import threading
import time

from common.log import logger

_runs = {}
_lock = threading.Lock()
OWNER_WORDS = ("me", "owner", "myself", "self")


def _receivers(task):
    action = task.get("action") or {}
    wanted = [r for r in [action.get("receiver"), *(action.get("receivers") or [])] if r]
    try:
        from channel.lrm_whatsapp_relay import task_recipients
        names = {r["receiver"]: r["name"] for r in task_recipients()}
    except Exception:
        names = {}
    out = []
    for r in dict.fromkeys(wanted):
        chat = str(r).rsplit(":", 1)[-1]
        out.append({"chat": chat, "digits": re.sub(r"\D", "", chat.split("@")[0]) if chat.endswith("@s.whatsapp.net") else "",
                    "group": chat.endswith("@g.us"), "name": names.get(r, "")})
    return out


def start(session_id, task, run_id=""):
    run = {"task_id": task.get("id"), "name": task.get("name") or "", "run_id": run_id or f"{task.get('id')}-{int(time.time())}",
           "receivers": _receivers(task), "started": time.time()}
    with _lock:
        _runs[session_id] = run
    return run


def finish(session_id):
    with _lock:
        _runs.pop(session_id, None)


def current(session_id):
    with _lock:
        return _runs.get(str(session_id or ""))


def _recipient_hit(run, to):
    """The run recipient a 'to' names, or None. Matches the owner, a phone (last 9 digits),
    a group jid/name, or a designated contact by name."""
    low = to.lower()
    digits = re.sub(r"\D", "", to)
    for r in run["receivers"]:
        name = r["name"].lower()
        if low in OWNER_WORDS and name.startswith("you (owner)"):
            return r
        if r["digits"] and digits and digits.endswith(r["digits"][-9:]):
            return r
        if r["group"]:
            wanted = low.removeprefix("group").strip(" :")
            group = name.removeprefix("group:").strip()
            if to == r["chat"] or (wanted and group and (wanted == group or (len(wanted) >= 3 and wanted in group))):
                return r
        elif name and len(low) >= 3 and (low == name or low in name or name in low):
            # a designated contact named in the send (no digits), e.g. "Hansel"
            return r
    return None


def scheduled_duplicate(session_id, tool_name, args):
    """During a scheduled run the scheduler delivers the final answer itself, so a whatsapp
    send from the agent would double-send and ask the owner for a needless approval. Refuse
    every whatsapp send during the run (not just ones we can match to a recipient); the agent
    puts the message in its final answer instead. Returns the refusal, or None."""
    run = current(session_id)
    if not run or tool_name != "whatsapp" or str((args or {}).get("action") or "").strip() != "send":
        return None
    to = str((args or {}).get("to") or "me").strip()
    hit = _recipient_hit(run, to)
    who = (hit["name"] or hit["chat"]) if hit else to
    logger.info(f"[TaskRuns] {run['task_id']}: whatsapp send to {who} during the run skipped")
    return (f"Not sent, and no approval was asked: this is the scheduled task '{run['name']}', and its result is "
            "delivered to the task's recipients automatically when the run ends. Put anything you want sent in your "
            "final answer instead of sending it yourself.")
