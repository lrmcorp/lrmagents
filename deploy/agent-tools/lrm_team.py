"""Who does what: every agent knows its teammates, so they can collaborate.

- Agents without a description get one from their role (CFO, HRD, Operations; Lorium the
  lead; Sora personal). A description the owner wrote is never replaced.
- The default agent still called "LRMagent" is renamed Lorium.
- Every hour (and at start) each agent's owner profile (USER.md) gets a "Your teammates"
  block: who the others are and what to ask them for, and the vault gets the note
  "10 Wiki/Who does what" with the same list. Both follow the Agents page: change a
  description there and the next refresh carries it everywhere.

Sora is listed for completeness but marked private: other agents can't ask her.
Installed as agent/lrm_team.py.
"""
import re
import threading
import time
from pathlib import Path

from common.log import logger

LEAD_NAME = "Lorium"
OLD_LEAD_NAMES = ("LRMagent", "CowAgent")
NOTE_PATH = "knowledge/10 Wiki/Who does what.md"
MARK = "<!-- lrm-team -->"

ROLES = (
    (r"\b(cfo|finance|keuangan|accounting)\b",
     "Finance: revenue, profit, cash, budgets and invoices; daily and month-to-date figures per business unit."),
    (r"\b(hrd|hr|people|sdm|human)\b",
     "People: attendance, payroll, staffing, recruitment, coach and staff schedules."),
    (r"\b(operations?|ops|operasional)\b",
     "Day-to-day running: bookings, class and PT sessions, branch status, cancellations and coverage."),
)
LEAD_DESC = "The lead: collects data from the other agents, checks vault updates and sends the owner's reports."
PERSONAL_DESC = "The owner's personal assistant (voice, camera, private memory). Private: other agents can't ask her."
BRANCH_DESC = ("Branch monitor: reads this branch's WhatsApp (Monitor only, never sends) and reports customer chats, "
               "new numbers, complaints, reply times, staff chat quality and sales (chat_monitor).")


def _admin():
    from agent.admin import get_agent_admin_service
    return get_agent_admin_service()


def _registry():
    from agent.registry import get_agent_registry
    return get_agent_registry()


def ensure_profiles():
    """Fill empty descriptions and rename the lead. Returns the changes made."""
    changes = []
    reg = _registry()
    lead = reg.default_agent_id
    try:
        from agent.lrm_personal import ID as PERSONAL
    except Exception:
        PERSONAL = "personal"
    for p in reg.list(include_disabled=True):
        update = {}
        if p.id == lead and p.name in OLD_LEAD_NAMES:
            update["name"] = LEAD_NAME
        if not (p.description or "").strip():
            if p.id == lead:
                update["description"] = LEAD_DESC
            elif p.id == PERSONAL:
                update["description"] = PERSONAL_DESC
            else:
                key = f"{p.id} {p.name}".lower().replace("-", " ").replace("_", " ")
                for pattern, desc in ROLES:
                    if re.search(pattern, key):
                        update["description"] = desc
                        break
        if update:
            try:
                _admin().update_agent(p.id, **update)
                changes.append((p.id, update))
            except Exception as e:
                logger.warning(f"[Team] could not update {p.id}: {e}")
    changes += _cheap_model_for_monitors(reg)
    if changes:
        logger.info(f"[Team] profiles updated: {changes}")
    return changes


def _cheap_model_for_monitors(reg):
    """Agents whose WhatsApp is Monitor only only digest chats: a fast, cheap model is plenty.
    Set once when the agent has no model of its own (the owner can change it after)."""
    import json as _json
    import os as _os
    import urllib.request as _ur
    try:
        from config import conf
        token = str(conf().get("external_api_token") or "")
        url = _os.environ.get("WHATSAPP_GATEWAY_URL", "http://whatsapp-gateway:8080").rstrip("/") + "/whatsapp/internal/status"
        with _ur.urlopen(_ur.Request(url, headers={"Authorization": f"Bearer {token}"}), timeout=5) as r:
            monitored = {a["agent"] for a in _json.loads(r.read()).get("accounts") or [] if a.get("mode") == "monitor"}
        from channel.web.api.lrm_voice import voice_model
        from channel.web.api.openai_compat import _model_catalog
        model = voice_model()
        provider = next((p["id"] for p in _model_catalog() if model in (p.get("models") or [])), None) if model else None
    except Exception as e:
        logger.debug(f"[Team] monitor model check skipped: {e}")
        return []
    done = []
    for agent_id in monitored:
        try:
            p = reg.get(agent_id, require_enabled=False)
        except Exception:
            continue
        update = {}
        if not (p.description or "").strip():
            update["description"] = BRANCH_DESC
        if not getattr(p, "model", None) and model and provider:
            update.update(model=model, bot_type=provider)
        if not update:
            continue
        try:
            _admin().update_agent(agent_id, **update)
            done.append((agent_id, update))
        except Exception as e:
            logger.warning(f"[Team] could not set the cheap model for {agent_id}: {e}")
    return done


def roster():
    reg = _registry()
    lead = reg.default_agent_id
    try:
        from agent.lrm_personal import ID as PERSONAL
    except Exception:
        PERSONAL = "personal"
    rows = []
    for p in reg.list(include_disabled=False):
        rows.append({"id": p.id, "name": p.name, "description": (p.description or "").strip(),
                     "lead": p.id == lead, "private": p.id == PERSONAL})
    rows.sort(key=lambda r: (not r["lead"], r["private"], r["name"].lower()))
    return rows


def _org():
    try:
        from agent import lrm_org
        return lrm_org.view()
    except Exception as e:
        logger.warning(f"[Team] org chart not read: {e}")
        return None


def _place_lines(me, org):
    """Where this agent sits on the Team page: who it reports to, who reports to it."""
    if not org:
        return []
    by_id = {r["id"]: r for r in org["agents"]}
    mine = by_id.get(me)
    if not mine or mine["private"]:
        return []
    team = [r for r in org["agents"] if r["reports_to"] == me]
    who = lambda r: f"{r['name']} [id {r['id']}]"
    lines = ["", "### Your place on the team (Team page)"]
    if mine["lead"]:
        if not team:
            return []
        lines.append("You are the lead. Level 1 agents report to you: " + ", ".join(
            who(r) + (f" ({r['group_name']})" if r["group_name"] else "") for r in team) + ".")
        lines.append("For a group's figures ask its Level 1 agent: it collects from its own Level 2 agents.")
        lines.append("You can read the memory of everyone on the chart with team_memory (list, search, read).")
        return lines
    if not mine["level"]:
        return []
    group = f" in {mine['group_name']}" if mine["group_name"] else ""
    boss = by_id.get(mine["reports_to"])
    if mine["level"] == 1:
        lines.append(f"You are Level 1{group}. You report to {who(boss) if boss else 'the lead'}.")
        if team:
            lines.append("Level 2 agents report to you: " + ", ".join(who(r) for r in team) + ". Collect their reports "
                         "with ask_agent (parallel calls), check and combine them, then report to the lead.")
            lines.append("Their memory (daily notes and chat notes) is open to you with team_memory: search it first, "
                         "then ask them only for what is missing or newer.")
    else:
        if boss:
            lines.append(f"You are Level 2{group}. You report to {who(boss)}: your reports and questions go to them "
                         "(ask_agent), not straight to the lead or the owner, unless the owner asks you directly.")
        else:
            lines.append(f"You are Level 2{group}, but nobody is set as your Level 1 yet: the owner sets it on the Team page.")
        lines.append("With ask_agent you can reach your Level 1 and the agents of your own group only.")
        lines.append("Keep notes: after each piece of work write what you found (figures, issues, complaints, decisions, "
                     "to-dos) into today's memory/YYYY-MM-DD.md. Your Level 1 reads your memory with team_memory, so "
                     "write it clearly, with dates and numbers. No passwords or codes in notes.")
    return lines


def _block(me, rows, org=None):
    lines = [MARK, "## Your teammates (LRMagent)",
             "Ask them for their own area with ask_agent (several at once with parallel calls) instead of fetching "
             "it yourself; answer them when they ask you. The same list is in the vault: 10 Wiki/Who does what."]
    places = {r["id"]: r for r in (org or {}).get("agents", [])}
    for r in rows:
        if r["id"] == me:
            continue
        if r["private"]:
            lines.append(f"- {r['name']}: the owner's personal assistant. Private: not reachable by other agents.")
            continue
        pl = places.get(r["id"]) or {}
        tag = " (the lead)" if r["lead"] else (f" (Level {pl['level']}{', ' + pl['group_name'] if pl.get('group_name') else ''})" if pl.get("level") else "")
        lines.append(f"- {r['name']}{tag} [id {r['id']}]: {r['description'] or 'no description yet: the owner sets it on the Agents page'}")
    lines += _place_lines(me, org)
    return "\n".join(lines) + "\n" + MARK + "\n"


def _note(rows, org=None):
    day = time.strftime("%d-%m-%Y")
    body = ["---", "tags: [team, agents]", f"updated: {time.strftime('%Y-%m-%d')}", "source: Agents and Team pages (refreshed every hour)", "---", "",
            "# Who does what", "",
            "Each agent's job and what it can be asked for. Kept up to date from the Agents page (descriptions) and the "
            "Team page (groups and levels): change them there, not here.", "", "| Agent | Role |", "|---|---|"]
    for r in rows:
        role = PERSONAL_DESC if r["private"] else (r["description"] or "(no description yet)")
        body.append(f"| **{r['name']}**{' (lead)' if r['lead'] else ''} | {role.replace('|', '/')} |")
    placed = [a for a in (org or {}).get("agents", []) if a["level"]]
    if placed:
        names = {a["id"]: a["name"] for a in org["agents"]}
        body += ["", "## Who reports to whom", ""]
        groups = [(g["id"], g["name"]) for g in org["groups"]] + [("", "No group")]
        for gid, gname in groups:
            members = [a for a in placed if a["group"] == gid]
            if not members:
                continue
            body.append(f"**{gname}**")
            for a in sorted(members, key=lambda a: (a["level"], a["name"].lower())):
                body.append(f"- Level {a['level']}: {a['name']} → reports to {names.get(a['reports_to'], 'nobody yet')}")
            body.append("")
    lead = next((r["name"] for r in rows if r["lead"]), LEAD_NAME)
    body += ["", "## How they work together", "",
             f"- {lead} collects each team's figures for the owner's reports by asking the agent responsible.",
             "- Level 2 agents report to their Level 1; Level 1 agents check, combine and report to the lead.",
             "- Any other agent may ask another for its own area; nobody but the owner reaches the personal assistant.",
             "- Finished results go to the vault through vault updates (the lead checks, the owner approves).",
             "", f"_Refreshed {day}._", ""]
    return "\n".join(body)


def refresh():
    """Teammate blocks in every USER.md and the vault note; writes only what changed."""
    rows = roster()
    org = _org()
    for r in rows:
        try:
            p = _registry().get(r["id"], require_enabled=False)
            path = Path(p.workspace) / "USER.md"
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            new = re.sub(re.escape(MARK) + r".*?" + re.escape(MARK) + r"\n?", "", text, flags=re.S).rstrip()
            new = (new + "\n\n" if new else "") + _block(r["id"], rows, org)
            if new != text:
                path.write_text(new, encoding="utf-8")
        except Exception as e:
            logger.warning(f"[Team] teammates block for {r['id']}: {e}")
    try:
        from agent.lrm_vault_flow import _vault
        vault = _vault()
        section, rel, full = vault.resolve(NOTE_PATH)
        note = _note(rows, org)
        old = full.read_text(encoding="utf-8") if full.exists() else ""
        # compare without the refresh date, so the note only changes when the team does
        strip = lambda t: re.sub(r"(updated: .*|_Refreshed .*_)", "", t)
        if strip(old) != strip(note):
            full.parent.mkdir(parents=True, exist_ok=True)
            from common.atomic_write import write_text_atomic
            write_text_atomic(full, note)
            try:
                vault.changed({section}, [rel])
            except Exception:
                pass
            logger.info("[Team] vault note 'Who does what' updated")
    except Exception as e:
        logger.warning(f"[Team] vault note: {e}")
    return rows


_started = False


def start_background():
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(20)
        while True:
            try:
                ensure_profiles()
                refresh()
            except Exception as e:
                logger.warning(f"[Team] refresh failed: {e}")
            time.sleep(3600)
    threading.Thread(target=loop, name="lrm-team", daemon=True).start()
