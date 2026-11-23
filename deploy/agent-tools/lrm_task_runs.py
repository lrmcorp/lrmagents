"""The scheduled run in progress, per session: its task, run id and recipients.

A scheduled task's final answer is delivered by the scheduler to the task's recipients, so a
whatsapp send to one of them during the run would send twice and ask the owner for an
approval nobody needs: lrm_activity.gate refuses it (no approval) with scheduled_duplicate().
Every tool call and approval of the run is logged with its run id (Activity page).
Installed as agent/lrm_task_runs.py; scheduler/integration.py calls start() and finish().

A run is also time-limited and can be stopped from the console: see guard() below.
"""
import os
import re
import threading
import time

from common.log import logger

_runs = {}
_lock = threading.Lock()
# The run this thread last finished: the scheduler classifies the answer right after finish().
_last = threading.local()
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
    slot = _slot()
    if slot is not None:
        with _lock:
            if slot.reason:
                # a stopped run must not start its retry or a second agent turn
                raise RuntimeError(f"run stopped: {slot.reason}")
            slot.sessions.add(str(session_id))
    run = {"task_id": task.get("id"), "name": task.get("name") or "", "run_id": run_id or f"{task.get('id')}-{int(time.time())}",
           "receivers": _receivers(task), "started": time.time(), "session": session_id}
    with _lock:
        _runs[session_id] = run
    return run


def finish(session_id):
    with _lock:
        _last.run = _runs.pop(session_id, None)


def note_refusal(session_id):
    """Record that an owner-only check refused this session's scheduled run (lrm_owner)."""
    with _lock:
        run = _runs.get(str(session_id or ""))
        if run is not None:
            run["refused"] = True


def was_refused(session_id=None):
    """Did the run hit an owner-only refusal? Without a session id: the run this thread last finished."""
    if session_id:
        with _lock:
            run = _runs.get(str(session_id))
        if run is not None:
            return bool(run.get("refused"))
    run = getattr(_last, "run", None)
    if run is None or (session_id and run.get("session") != str(session_id)):
        return False
    return bool(run.get("refused"))


def current(session_id):
    with _lock:
        return _runs.get(str(session_id or ""))


def _word_in(part, whole):
    return re.search(r"(?<!\w)" + re.escape(part) + r"(?!\w)", whole) is not None


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
        elif name and len(low) >= 3 and len(name) >= 3 and (low == name or _word_in(low, name) or _word_in(name, low)):
            # a designated contact named in the send (no digits), e.g. "Hansel"; whole words only, so "Al" ≠ "Halim"
            return r
    return None


def scheduled_duplicate(session_id, tool_name, args):
    """During a scheduled run the scheduler delivers the final answer to the task's recipients
    itself, so a whatsapp send to one of them would double-send and ask the owner for a needless
    approval. Only those sends are refused; a send to anyone else (e.g. Operations posting the
    branch report to the HR group) goes through the usual gate. Returns the refusal, or None."""
    run = current(session_id)
    if not run or tool_name != "whatsapp" or str((args or {}).get("action") or "").strip() != "send":
        return None
    to = str((args or {}).get("to") or "me").strip()
    hit = _recipient_hit(run, to)
    if not hit:
        return None
    who = hit["name"] or hit["chat"]
    logger.info(f"[TaskRuns] {run['task_id']}: whatsapp send to {who} during the run skipped")
    return (f"Not sent, and no approval was asked: this is the scheduled task '{run['name']}', and its result is "
            "delivered to the task's recipients automatically when the run ends. Put anything you want sent in your "
            "final answer instead of sending it yourself.")


# Time limit and Stop. The scheduler runs each task through guard(): the work runs on its own
# thread and the scheduler thread waits for it. Past the limit, or when the owner presses Stop,
# the run is marked failed, the agent's cancel flag is set (it stops at its next step or tool
# call), nothing is delivered, and guard() returns so the scheduler slot is free again, even if
# the work never returns: a Python thread cannot be killed, only asked to stop.
DEFAULT_LIMIT_MIN = 45
_slots = {}          # worker thread ident -> _Slot
_abandoned = set()   # worker threads of stopped runs that are still unwinding
_stopped_runs = set()


def limit_minutes():
    try:
        value = float(os.environ.get("LRMAGENT_RUN_TIMEOUT_MIN", "") or DEFAULT_LIMIT_MIN)
    except ValueError:
        value = DEFAULT_LIMIT_MIN
    return value if value > 0 else DEFAULT_LIMIT_MIN


class _Slot:
    def __init__(self, task):
        self.task = task or {}
        self.started = time.time()
        self.done = threading.Event()
        self.stop = threading.Event()
        self.reason = ""
        self.thread = None
        self.run_id = ""
        self.store = None
        self.agent_id = ""
        self.sessions = set()
        self.cancels = []


def _slot():
    with _lock:
        return _slots.get(threading.get_ident())


def guard(task, work, poll=1.0):
    """Run work() (one scheduled or manual run) under the time limit and the Stop button.
    Returns work()'s result, or True when the run was stopped (handled: no retry next tick)."""
    close_stale()
    slot = _Slot(task)
    out = {}

    def body():
        slot.thread = threading.get_ident()
        with _lock:
            _slots[slot.thread] = slot
        try:
            out["ok"] = work()
        except BaseException as e:  # noqa: BLE001 - handed back to the scheduler thread
            out["error"] = e
        finally:
            with _lock:
                _slots.pop(slot.thread, None)
                _abandoned.discard(slot.thread)
            slot.done.set()

    threading.Thread(target=body, name=f"lrm-run-{slot.task.get('id')}", daemon=True).start()
    limit = limit_minutes()
    deadline = slot.started + limit * 60
    while not slot.done.wait(max(0.01, min(poll, deadline - time.time()))):
        if slot.stop.is_set():
            break
        if time.time() >= deadline:
            _stop(slot, f"stopped after {limit:g} minutes", notify=True)
            break
    if slot.stop.is_set():
        return True
    if "error" in out:
        raise out["error"]
    return out.get("ok")


def attach(run, agent_id=""):
    """The run-history row of the current guarded run: (store, run_id) from _record_scheduler_run."""
    slot = _slot()
    if slot is None or not run:
        return
    with _lock:
        slot.store, slot.run_id = run[0], run[1]
        slot.agent_id = agent_id or ""
        reason = slot.reason
    if reason:
        _close_row(slot.store, slot.run_id, reason)


def bind_cancel(event):
    """Agent.run_stream's cancel flag (apply.py wrapper): set it when this run is stopped."""
    slot = _slot()
    if slot is None or event is None:
        return
    with _lock:
        slot.cancels.append(event)
        reason = slot.reason
    if reason:
        event.set()


def stopped():
    """Why the current guarded run was stopped, or "" (checked before delivering)."""
    slot = _slot()
    return slot.reason if slot is not None else ""


def close(store, run_id, **kwargs):
    """Close a run-history row unless it was already closed as stopped."""
    with _lock:
        if run_id in _stopped_runs:
            return False
    return store.finish_run(run_id, **kwargs)


def abandoned_threads():
    with _lock:
        return set(_abandoned)


def _close_row(store, run_id, reason):
    with _lock:
        _stopped_runs.add(run_id)
    try:
        store.finish_run(run_id, status="error", error=reason)
    except Exception as e:
        logger.warning(f"[TaskRuns] could not mark run {run_id} failed: {e}")


def _cancel_sessions(sessions):
    """Set the cancel token the bridge registered for the run's agent session(s)."""
    try:
        from agent.protocol.cancel import get_cancel_registry
        reg = get_cancel_registry()
        with reg._lock:
            keys = [k for k in list(reg._by_session) + list(reg._by_request)
                    if any(k == s or k.endswith("::" + s) for s in sessions)]
        for k in keys:
            reg.cancel_session(k)
            reg.cancel_request(k)
    except Exception as e:
        logger.debug(f"[TaskRuns] cancel registry unavailable: {e}")


def _stop(slot, reason, notify):
    with _lock:
        if slot.reason:
            return False
        slot.reason = reason
        if slot.thread and not slot.done.is_set():
            _abandoned.add(slot.thread)
        sessions, cancels = set(slot.sessions), list(slot.cancels)
        store, run_id, agent_id = slot.store, slot.run_id, slot.agent_id
    slot.stop.set()
    for event in cancels:
        event.set()
    if sessions:
        _cancel_sessions(sessions)
    if run_id:
        _close_row(store, run_id, reason)
    logger.warning(f"[TaskRuns] {slot.task.get('id')}: {reason}; nothing will be sent")
    if notify:
        try:
            from agent import lrm_task_output
            lrm_task_output.note_failed(agent_id or slot.task.get("agent_id") or "", slot.task, reason)
        except Exception:
            pass
    return True


def _store():
    from agent.memory import get_conversation_store
    return get_conversation_store()


def stop(run_id, reason="stopped by the owner"):
    """The console's Stop button. Returns "stopped" (a live run), "closed" (a row left Running by
    an earlier container or an abandoned run) or "" (no such running run)."""
    run_id = str(run_id or "")
    if not run_id:
        return ""
    with _lock:
        slot = next((s for s in _slots.values() if s.run_id == run_id), None)
    if slot is not None:
        return "stopped" if _stop(slot, reason, notify=False) else ""
    try:
        store = _store()
        row = store.get_run(run_id) if store is not None else None
    except Exception:
        row = None
    if not row or row.get("task_source") != "scheduler" or row.get("status") != "running":
        return ""
    _close_row(store, run_id, reason)
    return "closed"


def close_stale():
    """Scheduled runs still marked Running that no thread of this process runs (cut off by a restart)."""
    try:
        from agent.memory.conversation_store import _live_runs
        store = _store()
        if store is None:
            return 0
        rows = store.list_runs(task_source="scheduler", status="running", limit=500)
    except Exception as e:
        logger.debug(f"[TaskRuns] stale run check skipped: {e}")
        return 0
    closed = 0
    for row in rows:
        if row.get("run_id") and row["run_id"] not in _live_runs:
            _close_row(store, row["run_id"], "interrupted: LRMagent restarted during the run")
            closed += 1
    if closed:
        logger.info(f"[TaskRuns] {closed} run(s) left Running by a restart marked failed")
    return closed
