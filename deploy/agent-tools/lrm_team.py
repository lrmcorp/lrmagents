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
    if changes:
        logger.info(f"[Team] profiles updated: {changes}")
    return changes


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


def _block(me, rows):
    lines = [MARK, "## Your teammates (LRMagent)",
             "Ask them for their own area with ask_agent (several at once with parallel calls) instead of fetching "
             "it yourself; answer them when they ask you. The same list is in the vault: 10 Wiki/Who does what."]
    for r in rows:
        if r["id"] == me:
            continue
        if r["private"]:
            lines.append(f"- {r['name']}: the owner's personal assistant. Private: not reachable by other agents.")
            continue
        tag = " (the lead)" if r["lead"] else ""
        lines.append(f"- {r['name']}{tag} [id {r['id']}]: {r['description'] or 'no description yet: the owner sets it on the Agents page'}")
    return "\n".join(lines) + "\n" + MARK + "\n"


def _note(rows):
    day = time.strftime("%d-%m-%Y")
    body = ["---", "tags: [team, agents]", f"updated: {time.strftime('%Y-%m-%d')}", "source: Agents page (refreshed every hour)", "---", "",
            "# Who does what", "",
            "Each agent's job and what it can be asked for. Kept up to date from the Agents page: change a description "
            "there, not here.", "", "| Agent | Role |", "|---|---|"]
    for r in rows:
        role = PERSONAL_DESC if r["private"] else (r["description"] or "(no description yet)")
        body.append(f"| **{r['name']}**{' (lead)' if r['lead'] else ''} | {role.replace('|', '/')} |")
    body += ["", "## How they work together", "",
             f"- {next((r['name'] for r in rows if r['lead']), LEAD_NAME)} collects each team's figures for the owner's reports by asking the agent responsible.",
             "- Any agent may ask another for its own area; nobody but the owner reaches the personal assistant.",
             "- Finished results go to the vault through vault updates (the lead checks, the owner approves).",
             "", f"_Refreshed {day}._", ""]
    return "\n".join(body)


def refresh():
    """Teammate blocks in every USER.md and the vault note; writes only what changed."""
    rows = roster()
    for r in rows:
        try:
            p = _registry().get(r["id"], require_enabled=False)
            path = Path(p.workspace) / "USER.md"
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            new = re.sub(re.escape(MARK) + r".*?" + re.escape(MARK) + r"\n?", "", text, flags=re.S).rstrip()
            new = (new + "\n\n" if new else "") + _block(r["id"], rows)
            if new != text:
                path.write_text(new, encoding="utf-8")
        except Exception as e:
            logger.warning(f"[Team] teammates block for {r['id']}: {e}")
    try:
        from agent.lrm_vault_flow import _vault
        vault = _vault()
        section, rel, full = vault.resolve(NOTE_PATH)
        note = _note(rows)
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
