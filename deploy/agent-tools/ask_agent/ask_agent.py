"""ask_agent: one of the owner's agents asks another for data, a report or a status.

Lorium (the lead) collects each team's figures from CFO, HRD, Operations and the rest;
Sora (the personal assistant) gets reports for the owner. Only in the owner's own chats
and the scheduled tasks they set up. Sora is never asked by the other agents: the owner's
personal matters stay with her. The other agent answers in a separate conversation
(lrm-report:<agent>:<asker>:<day>), so its regular chats are not mixed up, and it sees only the
question, not the conversation around it.
"""
import concurrent.futures
import datetime
import json
import os
import socket
import time
import urllib.error
import urllib.request

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger
from config import conf

RETRY_DELAY = 3


def _timeout():
    try:
        return max(10, int(os.environ.get("LRMAGENT_ASK_TIMEOUT") or 180))
    except ValueError:
        return 180


def _timed_out(e):
    return isinstance(e, (socket.timeout, TimeoutError)) or isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError))


def _post(request, timeout):
    """(answer, error, timed_out). One retry on a connection error or 5xx only: after a timeout the
    other agent is probably still working, and asking again would double the wait."""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as r:
                return str(json.loads(r.read())["choices"][0]["message"]["content"]).strip(), "", False
        except urllib.error.HTTPError as e:
            try:
                why = json.loads(e.read() or b"{}").get("error", {}).get("message")
            except (ValueError, AttributeError):
                why = None
            if e.code < 500 or attempt == 2:
                return "", str(why or e.code), False
        except Exception as e:
            if _timed_out(e):
                return "", "", True
            if not isinstance(e, (urllib.error.URLError, ConnectionError)) or attempt == 2:
                return "", str(e), False
        time.sleep(RETRY_DELAY)
    return "", "no answer", False


class AskAgent(BaseTool):
    name: str = "ask_agent"
    description: str = (
        "Ask another of the owner's agents (CFO, HRD, Operations, Lorium, ...) for data, a report, a status or an "
        "answer: e.g. Lorium collecting each team's figures for the owner's daily report. Ask each agent for its own "
        "area instead of fetching that data yourself. Actions: 'list' (which agents exist and what they do), 'ask' "
        "(agent + question). Write the question fully (period, cut-off time, what to include): the other agent does "
        "not see this conversation. To ask several agents, use ONE 'ask' call with 'agents' (a list of ids, all getting "
        "'question', or of {agent, question} pairs): they answer at the same time and each answer comes back labelled. "
        "An agent that does not answer in time is marked 'no answer'; build the report from the answers that arrived."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "ask"]},
            "agent": {"type": "string", "description": "For 'ask': the agent id or name from 'list'"},
            "question": {"type": "string", "description": "For 'ask': the complete question"},
            "agents": {"type": "array", "description": "For 'ask' to several agents at once: agent ids/names (each gets "
                       "'question'), or objects {agent, question}",
                       "items": {"anyOf": [{"type": "string"}, {"type": "object", "properties": {
                           "agent": {"type": "string"}, "question": {"type": "string"}}, "required": ["agent"]}]}},
        },
        "required": ["action"],
    }

    def execute(self, args: dict) -> ToolResult:
        from agent.lrm_owner import REFUSAL, owner_session
        from agent.lrm_personal import ID, NAME
        model = getattr(self, "model", None)
        me = str(getattr(model, "agent_id", "") or "")
        if not owner_session(str(getattr(model, "session_id", "") or ""))[0]:
            return ToolResult.fail(REFUSAL)
        from agent.registry import get_agent_registry
        agents = get_agent_registry().list(include_disabled=False)
        if not me:
            me = get_agent_registry().default_agent_id
        caller = next((p.name for p in agents if p.id == me), me) if me != ID else NAME
        # Nobody asks themselves; only the owner reaches Sora (her matters are private).
        others = [p for p in agents if p.id != me and p.id != ID]
        action = (args.get("action") or "").strip()
        # The Team page: a Level 2 agent reaches only its Level 1 and its own group.
        try:
            from agent import lrm_org
            org = lrm_org.view()
            places = {r["id"]: r for r in org["agents"]}
            reachable = lambda target_id: lrm_org.can_ask(me, target_id, org)
        except Exception as e:
            logger.warning(f"[ask_agent] org chart not read: {e}")
            places, reachable = {}, (lambda target_id: (True, ""))
        if action == "list":
            if not others:
                return ToolResult.success("There are no other agents yet.")

            def line(p):
                pl = places.get(p.id) or {}
                where = f" [Level {pl['level']}{', ' + pl['group_name'] if pl.get('group_name') else ''}]" if pl.get("level") else ""
                return f"- {p.id}: {p.name}{where}" + (f" ({p.description})" if getattr(p, "description", None) else "")
            mine = [p for p in others if reachable(p.id)[0]]
            text = "Agents:\n" + "\n".join(line(p) for p in mine)
            if len(mine) < len(others):
                text += "\n\n" + reachable(next(p.id for p in others if not reachable(p.id)[0]))[1]
            return ToolResult.success(text)
        if action != "ask":
            return ToolResult.fail("action must be 'list' or 'ask'.")
        token = str(conf().get("external_api_token") or "")
        port = int(conf().get("web_port") or os.environ.get("WEB_PORT") or 9899)
        day = datetime.date.today().isoformat()
        timeout = _timeout()

        def resolve(name, question):
            """(target, question, None) or (None, label, error)."""
            wanted = str(name or "").strip().lower()
            question = str(question or "").strip()
            target = next((p for p in others if p.id.lower() == wanted or (p.name or "").lower() == wanted), None)
            if target is None:
                return None, str(name or "?"), "No such agent. Use action 'list' first."
            allowed, why = reachable(target.id)
            if not allowed:
                return None, target.name, why
            if not question:
                return None, target.name, "Say what to ask."
            return target, question, None

        def ask_one(target, question):
            # One conversation per asker and day: Sora's and Lorium's questions must not share a history.
            body = {"agent_id": target.id, "conversation_id": f"lrm-report:{target.id}:{me or 'default'}:{day}", "stream": False,
                    "messages": [{"role": "user", "content": f"[{caller} asks on the owner's behalf. Answer with the data and facts asked for, "
                                                             f"as a title and points; say plainly what you could not get and why.]\n{question}"}]}
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", method="POST", data=json.dumps(body).encode(),
                                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            answer, why, timed_out = _post(request, timeout)
            if timed_out:
                logger.warning(f"[ask_agent] {target.id}: no answer within {timeout} s")
                return False, f"{target.name}: no answer within {timeout} s (not asked again; it may still be working)."
            if why:
                logger.warning(f"[ask_agent] {target.id}: {why}")
                return False, f"{target.name} could not answer: {why}"
            return True, f"{target.name} answered:\n{answer}"

        many = args.get("agents")
        if not many:
            target, question, error = resolve(args.get("agent"), args.get("question"))
            if error:
                return ToolResult.fail(error)
            ok, text = ask_one(target, question)
            return ToolResult.success(text) if ok else ToolResult.fail(text)
        if not isinstance(many, list):
            return ToolResult.fail("'agents' must be a list.")
        jobs, lines = [], []
        for item in many:
            name, q = (item.get("agent"), item.get("question") or args.get("question")) if isinstance(item, dict) else (item, args.get("question"))
            target, question, error = resolve(name, q)
            if error:
                lines.append(f"{question}: {error}")
            elif all(t.id != target.id for t, _ in jobs):
                jobs.append((target, question))
        # Asked at the same time, so the slowest agent sets the wait, not the sum of them all.
        results = []
        if jobs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                results = list(pool.map(lambda job: ask_one(*job), jobs))
        text = "\n\n".join([t for _, t in results] + lines)
        return ToolResult.success(text) if any(ok for ok, _ in results) else ToolResult.fail(text)
