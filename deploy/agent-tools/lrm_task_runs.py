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


def scheduled_duplicate(session_id, tool_name, args):
    """The refusal for a whatsapp send to one of this run's own recipients, else None."""
    run = current(session_id)
    if not run or tool_name != "whatsapp" or str((args or {}).get("action") or "").strip() != "send":
        return None
    to = str((args or {}).get("to") or "me").strip()
    low = to.lower()
    hit = None
    for r in run["receivers"]:
        name = r["name"].lower()
        if low in OWNER_WORDS and name.startswith("you (owner)"):
            hit = r
        elif r["digits"] and re.sub(r"\D", "", to) and re.sub(r"\D", "", to).endswith(r["digits"][-9:]):
            hit = r
        elif r["group"]:
            wanted = low.removeprefix("group").strip(" :")
            group = name.removeprefix("group:").strip()
            if to == r["chat"] or (wanted and group and (wanted == group or (len(wanted) >= 3 and wanted in group))):
                hit = r
        if hit:
            break
    if not hit:
        return None
    who = hit["name"] or hit["chat"]
    logger.info(f"[TaskRuns] {run['task_id']}: whatsapp send to its own recipient {who} skipped")
    return (f"Not sent, and no approval was asked: this is the scheduled task '{run['name']}', and its final answer is "
            f"delivered to {who} automatically when the run ends. Put the message in your final answer instead of "
            "sending it yourself.")
