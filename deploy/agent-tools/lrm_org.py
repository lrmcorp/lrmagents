"""The owner's org chart: groups (LRM, HR Group, ...) and who reports to whom.

- Lorium (the default agent) is the lead, at the top.
- Level 1 agents report to the lead; Level 2 agents report to a Level 1 agent.
- Agents not placed keep working as before (they may ask anyone).
- A Level 2 agent asks only its own Level 1 and the agents of its own group (ask_agent):
  its reports go up the chain, not straight to the lead. The lead and Level 1 may ask anyone.
- The personal assistant (Sora) is never placed: she stays private.

Stored in <data root>/lrm_org.json; the Team page edits it through /api/lrm/org.
Installed as agent/lrm_org.py.
"""
import json
import os
import re
import threading
from pathlib import Path

_lock = threading.Lock()
LEVELS = (1, 2)


def _file():
    from config import get_data_root
    return Path(get_data_root()) / "lrm_org.json"


def load():
    try:
        doc = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        doc = {}
    return {"groups": list(doc.get("groups") or []), "agents": dict(doc.get("agents") or {})}


def _agents():
    from agent.registry import get_agent_registry
    reg = get_agent_registry()
    try:
        from agent.lrm_personal import ID as personal
    except Exception:
        personal = "personal"
    return reg.default_agent_id, personal, [p for p in reg.list(include_disabled=False)]


def _slug(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "group"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def save(groups, placements):
    """Validate and store. groups: [{id?, key?, name}]; placements: {agent_id: {group, level, reports_to}},
    where group is a group's id, or the key the page gave a group that is new (no id yet)."""
    lead, personal, agents = _agents()
    known = {p.id for p in agents}
    out_groups, ids, keys = [], set(), {}
    for g in groups or []:
        name = str(g.get("name") or "").strip()[:40]
        if not name:
            continue
        gid = str(g.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9-]{1,40}", gid) or gid in ids:
            gid = _slug(name, ids)
        ids.add(gid)
        keys[str(g.get("key") or gid)] = gid
        keys[gid] = gid
        out_groups.append({"id": gid, "name": name})
    if len({g["name"].lower() for g in out_groups}) != len(out_groups):
        raise ValueError("Two groups have the same name.")
    out = {}
    for agent_id, p in (placements or {}).items():
        if agent_id not in known or agent_id in (lead, personal):
            continue
        try:
            level = int(p.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        group = keys.get(str(p.get("group") or ""), "")
        if level not in LEVELS and not group:
            continue
        out[agent_id] = {"group": group, "level": level if level in LEVELS else 0, "reports_to": str(p.get("reports_to") or "")}
    names = {p.id: p.name for p in agents}
    for agent_id, p in out.items():
        if p["level"] == 2:
            boss = out.get(p["reports_to"])
            if not boss or boss["level"] != 1:
                raise ValueError(f"{names.get(agent_id, agent_id)} is Level 2: choose the Level 1 agent it reports to.")
        else:
            p["reports_to"] = ""
    with _lock:
        path = _file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"groups": out_groups, "agents": out}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return view()


def view():
    """Every agent with its place, for the Team page and the teammates blocks."""
    lead, personal, agents = _agents()
    doc = load()
    groups = {g["id"]: g["name"] for g in doc["groups"]}
    rows = []
    for p in agents:
        place = doc["agents"].get(p.id) or {}
        level = place.get("level") if place.get("level") in LEVELS else 0
        group = place.get("group") if place.get("group") in groups else ""
        if p.id == lead:
            boss = ""
        elif level == 1:
            boss = lead
        elif level == 2:
            boss = place.get("reports_to") or ""
        else:
            boss = ""
        rows.append({"id": p.id, "name": p.name or p.id, "description": (p.description or "").strip(),
                     "lead": p.id == lead, "private": p.id == personal, "level": 0 if p.id in (lead, personal) else level,
                     "group": "" if p.id in (lead, personal) else group, "group_name": groups.get(group, ""),
                     "reports_to": boss})
    # a Level 2 whose Level 1 was removed or moved down reports to nobody until the owner fixes it
    level1 = {r["id"] for r in rows if r["level"] == 1}
    for r in rows:
        if r["level"] == 2 and r["reports_to"] not in level1:
            r["reports_to"] = ""
    return {"lead": lead, "groups": doc["groups"], "agents": rows}


def place(agent_id, v=None):
    v = v or view()
    return next((r for r in v["agents"] if r["id"] == agent_id), None)


def team_of(agent_id, v=None):
    """The agents reporting directly to agent_id (for the lead: the Level 1 agents)."""
    v = v or view()
    return [r for r in v["agents"] if r["reports_to"] == agent_id and r["id"] != agent_id]


def can_ask(me, target_id, v=None):
    """(allowed, reason). Only Level 2 agents are limited: their manager and their own group."""
    v = v or view()
    mine = place(me, v)
    if not mine or mine["level"] != 2:
        return True, ""
    target = place(target_id, v)
    if target and (target["id"] == mine["reports_to"] or (mine["group"] and target["group"] == mine["group"])):
        return True, ""
    boss = place(mine["reports_to"], v)
    if boss:
        return False, (f"You are Level 2 and report to {boss['name']} [id {boss['id']}]: send it to them "
                       f"(ask_agent agent={boss['id']}), and they take it further.")
    return False, "You are Level 2 but have no Level 1 agent to report to yet: the owner sets it on the Team page."


def below(agent_id, v=None):
    """Everyone under agent_id on the chart: a Level 1's Level 2 agents; for the lead, every placed agent."""
    v = v or view()
    out, todo = [], [agent_id]
    while todo:
        boss = todo.pop()
        for r in team_of(boss, v):
            if r["id"] not in {o["id"] for o in out} and r["id"] != agent_id and not r["private"]:
                out.append(r)
                todo.append(r["id"])
    return out
