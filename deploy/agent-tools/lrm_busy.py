"""How many agent runs are in progress, so a redeploy can wait for them instead of
cutting a report off halfway (the deploy workflow asks /api/lrm/busy before restarting).

Agent.run_stream is wrapped (deploy/brand/apply.py) to count every run: console chats,
WhatsApp, scheduled tasks, ask_agent, vault reviews. A scheduled run stopped by its time limit
or the Stop button is not counted while its thread unwinds. Installed as agent/lrm_busy.py.
"""
import contextlib
import threading
import time

_lock = threading.Lock()
_runs = {}


@contextlib.contextmanager
def running(label=""):
    key = object()
    with _lock:
        _runs[key] = (time.time(), label, threading.get_ident())
    try:
        yield
    finally:
        with _lock:
            _runs.pop(key, None)


def state():
    try:
        from agent import lrm_task_runs
        abandoned = lrm_task_runs.abandoned_threads()
    except Exception:
        abandoned = set()
    with _lock:
        started = [t for t, _, thread in _runs.values() if thread not in abandoned]
    scheduled = 0
    try:
        from agent.tools.scheduler import integration
        svc = getattr(integration, "_scheduler_service", None)
        scheduled = len(getattr(svc, "_active_task_ids", ()) or ())
    except Exception:
        pass
    return {"runs": len(started), "scheduled": scheduled, "stopped": len(abandoned),
            "longest_seconds": int(time.time() - min(started)) if started else 0}
