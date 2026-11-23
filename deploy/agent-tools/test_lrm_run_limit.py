"""Stubbed tests for the scheduled-run time limit and Stop button (python3 test_lrm_run_limit.py).

A hung run used to hold one of the scheduler's 4 task slots forever. guard() now ends the
wait at the limit (or on Stop): the run is marked failed, the agent's cancel flag is set,
the owner is told, nothing is delivered and the slot is free. Runs standalone: the
container modules (run history store, cancel registry, owner notice) are stubbed.
"""
import os
import sys
import threading
import time
import types


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None)
_mod("channel")
_mod("channel.lrm_whatsapp_relay").task_recipients = lambda: []
agent = _mod("agent")
memory = _mod("agent.memory")
store_mod = _mod("agent.memory.conversation_store")
store_mod._live_runs = set()
_mod("agent.protocol")
cancel_mod = _mod("agent.protocol.cancel")
NOTES = []
_mod("agent.lrm_task_output").note_failed = lambda agent_id, task, reason: NOTES.append((agent_id, task.get("id"), reason))
agent.lrm_task_output = sys.modules["agent.lrm_task_output"]


class Store:
    def __init__(self):
        self.rows = {}

    def add(self, run_id, status="running"):
        self.rows[run_id] = {"run_id": run_id, "task_source": "scheduler", "status": status, "error": ""}

    def finish_run(self, run_id, status="done", error="", extras=None):
        row = self.rows.get(run_id)
        if row is None:
            return False
        row.update(status=status, error=error)
        return True

    def get_run(self, run_id):
        return self.rows.get(run_id)

    def list_runs(self, task_source=None, status=None, limit=100):
        return [dict(r) for r in self.rows.values() if r["task_source"] == task_source and r["status"] == status][:limit]


STORE = Store()
memory.get_conversation_store = lambda: STORE


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_session = {}
        self._by_request = {}
        self.cancelled = []

    def cancel_session(self, key):
        if key in self._by_session:
            self.cancelled.append(key)
            self._by_session[key].set()

    def cancel_request(self, key):
        if key in self._by_request:
            self._by_request[key].set()


REG = Registry()
cancel_mod.get_cancel_registry = lambda: REG

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_task_runs as runs  # noqa: E402
agent.lrm_task_runs = runs
import lrm_busy  # noqa: E402

PASS = {"f": 0}


def check(name, got, want):
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def hung_work(task, run_id, release, seen):
    """A scheduled run that hangs inside the agent: it registers like integration.py does."""
    def work():
        sid = f"scheduler_{run_id}_{task['id']}"
        STORE.add(run_id)
        runs.attach((STORE, run_id), "ops")
        runs.start(sid, task, run_id)
        ev = threading.Event()
        REG._by_session["ops::" + sid] = ev
        runs.bind_cancel(ev)
        with lrm_busy.running("ops"):
            seen["entered"] = True
            release.wait(5)
            seen["cancel_set"] = ev.is_set()
            seen["stopped_inside"] = runs.stopped()
        runs.finish(sid)
        # what integration.py does after the agent returns: the row is not overwritten as done,
        # a retry may not start, and delivery is skipped because stopped() is set
        seen["closed_again"] = runs.close(STORE, run_id, status="done", error="")
        try:
            runs.start(sid, task, run_id)
            seen["retry"] = "started"
        except RuntimeError:
            seen["retry"] = "refused"
        return "delivered" if not runs.stopped() else "skipped"
    return work


# 1. defaults
os.environ.pop("LRMAGENT_RUN_TIMEOUT_MIN", None)
check("default limit is 45 minutes", runs.limit_minutes(), 45)
os.environ["LRMAGENT_RUN_TIMEOUT_MIN"] = "abc"
check("a bad limit falls back to 45", runs.limit_minutes(), 45)
os.environ["LRMAGENT_RUN_TIMEOUT_MIN"] = "0"
check("a zero limit falls back to 45", runs.limit_minutes(), 45)

# 2. a normal run returns its own result
os.environ["LRMAGENT_RUN_TIMEOUT_MIN"] = "1"
check("a run inside the limit returns its result", runs.guard({"id": "t0"}, lambda: "ok", poll=0.01), "ok")
check("a failed run is still reported as failed", runs.guard({"id": "t0"}, lambda: False, poll=0.01), False)

# 3. the time limit
os.environ["LRMAGENT_RUN_TIMEOUT_MIN"] = "0.005"  # 0.3 s
release, seen = threading.Event(), {}
task = {"id": "t1", "name": "Daily report", "agent_id": "ops"}
t0 = time.time()
got = runs.guard(task, hung_work(task, "r1", release, seen), poll=0.02)
check("a hung run returns at the limit", (got, time.time() - t0 < 2), (True, True))
check("its history row is failed with the reason", (STORE.rows["r1"]["status"], STORE.rows["r1"]["error"]),
      ("error", "stopped after 0.005 minutes"))
check("the owner is told once", NOTES, [("ops", "t1", "stopped after 0.005 minutes")])
check("the agent's cancel token is set", REG.cancelled, ["ops::scheduler_r1_t1"])
check("the busy count leaves the stopped run out", (lrm_busy.state()["runs"], lrm_busy.state()["stopped"]), (0, 1))
release.set()
deadline = time.time() + 3
while "retry" not in seen and time.time() < deadline:
    time.sleep(0.01)
check("the agent saw its cancel flag", seen.get("cancel_set"), True)
check("the run knows it was stopped", seen.get("stopped_inside"), "stopped after 0.005 minutes")
check("the late finish does not overwrite the failed row", (seen.get("closed_again"), STORE.rows["r1"]["status"]), (False, "error"))
check("no retry starts after a stop", seen.get("retry"), "refused")
time.sleep(0.05)
check("the unwound thread is no longer tracked", runs.abandoned_threads(), set())

# 4. the slot is free again: a second task gets the scheduler's only slot after the first hangs
os.environ["LRMAGENT_RUN_TIMEOUT_MIN"] = "0.003"
slot, order = threading.BoundedSemaphore(1), []
stuck = threading.Event()


def run_due(tid, work):
    with slot:
        order.append((tid, runs.guard({"id": tid}, work, poll=0.02)))


a = threading.Thread(target=run_due, args=("hung", lambda: stuck.wait(5)))
b = threading.Thread(target=run_due, args=("next", lambda: "done"))
a.start(); time.sleep(0.05); b.start()
b.join(3)
check("the next task runs after the hung one hits the limit", order, [("hung", True), ("next", "done")])
stuck.set(); a.join(1)

# 5. the Stop button
os.environ["LRMAGENT_RUN_TIMEOUT_MIN"] = "1"
NOTES.clear()
release, seen, res = threading.Event(), {}, {}
task = {"id": "t2", "name": "Weekly", "agent_id": "ops"}
g = threading.Thread(target=lambda: res.setdefault("got", runs.guard(task, hung_work(task, "r2", release, seen), poll=0.02)))
g.start()
deadline = time.time() + 2
while not seen.get("entered") and time.time() < deadline:
    time.sleep(0.01)
check("Stop on a live run", runs.stop("r2"), "stopped")
g.join(2)
check("the scheduler is released at once", res.get("got"), True)
check("the stopped row says why", (STORE.rows["r2"]["status"], STORE.rows["r2"]["error"]), ("error", "stopped by the owner"))
check("the owner is not messaged about their own Stop", NOTES, [])
check("Stop twice: not running any more", runs.stop("r2"), "")
release.set()

# 6. rows a restart left Running
STORE.add("r3")
check("Stop on a row left Running closes it", (runs.stop("r3"), STORE.rows["r3"]["status"]), ("closed", "error"))
check("Stop on an unknown run", runs.stop("nope"), "")
STORE.add("r4")
STORE.add("r5")
store_mod._live_runs.add("r5")
check("close_stale closes only rows no thread runs", runs.close_stale(), 1)
check("the stale row is marked interrupted", (STORE.rows["r4"]["status"], STORE.rows["r4"]["error"]),
      ("error", "interrupted: LRMagent restarted during the run"))
check("a live row stays Running", STORE.rows["r5"]["status"], "running")

print("FAILED" if PASS["f"] else "all passed")
sys.exit(1 if PASS["f"] else 0)
