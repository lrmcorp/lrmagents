"""A browser tab per scheduled task and per agent-to-agent request.

The agents share one browser and upstream drives a single page, so two reports running at the
same time would navigate each other's page. Runs whose session is a scheduled task
(scheduler_...) or an ask_agent request (lrm-report:...) get a tab of their own; the owner's
chats keep the main tab, which the Browser page shows. One tool call runs at a time (the
browser itself is serial anyway), with the page switched to the caller's tab for that call.
Installed as agent/lrm_browser_tabs.py; browser_tool.execute is patched to call run().
"""
import threading
import time

from common.log import logger

_lock = threading.RLock()
OWN_TAB = ("scheduler_", "lrm-report:", "openai:conversation:lrm-report:")
IDLE_CLOSE = 30 * 60


def _key(tool):
    session = str(getattr(getattr(tool, "model", None), "session_id", "") or "")
    return session if session.startswith(OWN_TAB) else ""


def _switch(service, key):
    """On the browser thread: make the caller's tab the current page."""
    ctx = service._context
    state = getattr(service, "_lrm_tabs", None)
    if state is None or state.get("ctx") is not ctx:
        state = service._lrm_tabs = {"ctx": ctx, "main": service._page, "tabs": {}}
    if state["main"] is None or state["main"].is_closed():
        state["main"] = service._page
    now = time.time()
    for k, (page, used) in list(state["tabs"].items()):
        if k != key and now - used > IDLE_CLOSE:
            try:
                page.close()
            except Exception:
                pass
            state["tabs"].pop(k, None)
    page = state["tabs"].get(key, (None, 0))[0]
    if page is None or page.is_closed():
        page = ctx.new_page()
    state["tabs"][key] = (page, now)
    service._page = page


def _restore(service):
    state = getattr(service, "_lrm_tabs", None)
    if state and state.get("ctx") is service._context and state["main"] is not None and not state["main"].is_closed():
        service._page = state["main"]


def run(tool, handler, args):
    key = _key(tool)
    with _lock:
        if not key:
            return handler(tool, args)
        service = tool._get_service()
        try:
            service._submit(_switch, service, key)
        except Exception as e:
            logger.warning(f"[BrowserTabs] own tab not opened, using the main one: {e}")
            return handler(tool, args)
        try:
            return handler(tool, args)
        finally:
            try:
                service._submit(_restore, service)
            except Exception:
                pass
