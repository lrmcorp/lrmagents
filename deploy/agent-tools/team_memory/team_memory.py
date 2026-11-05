"""team_memory: a manager reads the memory of the agents under it on the Team page.

A Level 1 agent reads its Level 2 agents' memory (MEMORY.md and memory/, the daily notes and
chat notes shown under memory/ in their Vault); the lead reads everyone placed on the chart.
Read only, never the personal assistant, and only in the owner's own chats and scheduled tasks.
"""
import datetime
import os
import re
from pathlib import Path

from agent.tools.base_tool import BaseTool, ToolResult

MAX_READ = 20_000
MAX_HITS = 12


def _files(workspace):
    ws = Path(workspace)
    top = ws / "MEMORY.md"
    if top.is_file():
        yield top
    mem = ws / "memory"
    if mem.is_dir():
        for p in mem.rglob("*.md"):
            if p.is_file() and ".trash" not in p.parts:
                yield p


def _rel(workspace, path):
    return str(Path(path).relative_to(workspace))


class TeamMemory(BaseTool):
    name: str = "team_memory"
    description: str = (
        "Read the memory of the agents who report to you on the Team page (a Level 1 agent: its Level 2 agents; "
        "the lead: everyone on the chart): their MEMORY.md, daily notes (memory/YYYY-MM-DD.md) and chat notes. "
        "Use it to check what they found, before asking them again. Actions: 'list' (whose memory you can read and "
        "their latest notes), 'search' (query, optional agent and days), 'read' (agent and path from list or search). "
        "Read only."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "search", "read"]},
            "agent": {"type": "string", "description": "Agent id or name (optional for 'search': all of them)"},
            "query": {"type": "string", "description": "For 'search': words to look for"},
            "days": {"type": "integer", "description": "For 'search': only notes changed in the last N days (default: all)"},
            "path": {"type": "string", "description": "For 'read': MEMORY.md or memory/... as listed"},
        },
        "required": ["action"],
    }

    def execute(self, args: dict) -> ToolResult:
        from agent.lrm_owner import REFUSAL, owner_session
        from agent import lrm_org
        from agent.registry import get_agent_registry
        model = getattr(self, "model", None)
        if not owner_session(str(getattr(model, "session_id", "") or ""))[0]:
            return ToolResult.fail(REFUSAL)
        reg = get_agent_registry()
        me = str(getattr(model, "agent_id", "") or "") or reg.default_agent_id
        team = lrm_org.below(me)
        if not team:
            return ToolResult.fail("Nobody reports to you on the Team page, so there is no team memory to read.")
        spaces = {}
        for r in team:
            try:
                spaces[r["id"]] = (r["name"], reg.get(r["id"], require_enabled=False).workspace)
            except Exception:
                continue
        action = (args.get("action") or "").strip()
        wanted = str(args.get("agent") or "").strip().lower()
        chosen = {i: v for i, v in spaces.items() if not wanted or i.lower() == wanted or v[0].lower() == wanted}
        if wanted and not chosen:
            return ToolResult.fail("Not one of your team: " + ", ".join(f"{n} [id {i}]" for i, (n, _) in spaces.items()))

        if action == "list":
            out = []
            for i, (name, ws) in chosen.items():
                notes = sorted(_files(ws), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
                out.append(f"### {name} [id {i}]")
                out += [f"- {_rel(ws, p)} (updated {datetime.datetime.fromtimestamp(p.stat().st_mtime):%d-%m-%Y %H:%M})" for p in notes] or ["- (no notes yet)"]
            return ToolResult.success("\n".join(out))

        if action == "search":
            terms = [t for t in re.findall(r"\w+", str(args.get("query") or "").lower()) if len(t) > 1]
            if not terms:
                return ToolResult.fail("Say what to look for (query).")
            since = None
            if args.get("days"):
                since = datetime.datetime.now().timestamp() - int(args["days"]) * 86400
            hits = []
            for i, (name, ws) in chosen.items():
                for p in _files(ws):
                    st = p.stat()
                    if since and st.st_mtime < since:
                        continue
                    try:
                        text = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    low = text.lower()
                    score = sum(low.count(t) for t in terms)
                    if not score or not all(t in low for t in terms[:3]):
                        continue
                    at = min((low.find(t) for t in terms if t in low), default=0)
                    snippet = re.sub(r"\s+", " ", text[max(0, at - 120): at + 280]).strip()
                    hits.append((score, st.st_mtime, name, i, _rel(ws, p), snippet))
            if not hits:
                return ToolResult.success("Nothing found in your team's memory.")
            hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
            return ToolResult.success("\n".join(f"- {n} [id {i}] {path}: …{snip}…" for _, _, n, i, path, snip in hits[:MAX_HITS]))

        if action == "read":
            if len(chosen) != 1:
                return ToolResult.fail("Say whose note (agent).")
            (i, (name, ws)), = chosen.items()
            rel = str(args.get("path") or "").strip().lstrip("/")
            full = (Path(ws) / rel).resolve()
            base = Path(ws).resolve()
            if not (full == base / "MEMORY.md" or str(full).startswith(str(base / "memory") + os.sep)) or full.suffix != ".md" or not full.is_file():
                return ToolResult.fail("Only MEMORY.md and notes under memory/ can be read (use list or search for the path).")
            text = full.read_text(encoding="utf-8", errors="replace")
            more = f"\n\n[cut at {MAX_READ} characters of {len(text)}]" if len(text) > MAX_READ else ""
            return ToolResult.success(f"{name} · {rel}\n\n{text[:MAX_READ]}{more}")

        return ToolResult.fail("action must be list, search or read.")
