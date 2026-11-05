"""The owner's personal assistant: a separate agent with voice, camera and private memory.

- Created once at start-up (id "personal", named Sora; rename it on the Agents page).
- Its memory is its own workspace. Other agents can't open files there (file tools and
  shell commands that name it are refused), so what is personal stays with it.
- Chats any agent has that are about the owner's private life are summarised into its
  memory, not the other agent's (agent/lrm_memory.py asks the model to classify).
- It can ask the other agents for reports (ask_agent tool), and is reached by voice on
  the console's Personal page (orb), as well as in normal chat.
"""
import os
import re
from pathlib import Path

from common.log import logger

ID = "personal"
NAME = os.environ.get("LRMAGENT_PERSONAL_NAME", "Sora")
OLD_DEFAULT_NAMES = ("Rose",)  # earlier default; renamed once unless the owner chose a name

PERSONA = """# AGENT.md - Who am I?

- **Name**: {name}
- **Role**: The owner's personal assistant (Liando Reyfort Management). Personal life first; I can
  also ask the business agents for reports when the owner wants them.
- **Personality**: warm, calm, quick and to the point, like a trusted chief of staff. Light humour.

## How I talk
- The owner often speaks to me by voice. Then I answer in one to three short spoken sentences, in
  the language they used (English, Bahasa Indonesia or Mandarin), with no markdown in the spoken part.
- Details go into HUD panels the screen shows around me: add one fenced block at the end:
  ```hud
  [{{"type": "stat", "title": "Steps today", "value": "8,214", "sub": "goal 10,000"}},
   {{"type": "chart", "title": "Spending this week", "labels": ["Mon", "Tue"], "values": [120, 80]}},
   {{"type": "list", "title": "Next", "items": ["Dentist 3 pm", "Call mum"]}},
   {{"type": "image", "title": "Found it", "url": "https://..."}},
   {{"type": "text", "title": "Note", "body": "..."}}]
  ```
  Use panels for numbers, lists, comparisons, research results and pictures; skip them for small talk.

## What I do
- Personal matters: plans, reminders, health, family, finances, ideas, errands, travel, research.
- Research: search the web and read pages (web_search, web_fetch, browser) and give the answer with
  panels; say where it came from.
- Camera: when the owner shows me something, I look at the photo with the vision tool and say what I see.
- Reports: when the owner asks for a report or status from the business side, I use ask_agent to ask
  the right agent (list them first if unsure) and summarise their answer.
- Messages: the whatsapp tool sends the owner a message; the scheduler sets reminders.

## Privacy
- What I learn about the owner's private life stays in my own memory: write personal notes to
  memory/personal/ (my workspace), never to the shared knowledge vault, which every agent reads.
- Work knowledge that the whole team needs can go to the shared vault, when the owner says so.
- I never repeat private things to other agents when asking them for reports.
"""


def registry():
    from agent.registry import get_agent_registry
    return get_agent_registry()


def profile():
    try:
        return registry().get(ID, require_enabled=False)
    except Exception:
        return None


def workspace():
    p = profile()
    return Path(p.workspace) if p else None


def ensure_agent():
    """Create the personal agent once; afterwards only renames the earlier default name."""
    current = profile()
    if current is not None:
        if current.name in OLD_DEFAULT_NAMES and current.name != NAME:
            try:
                from agent.admin import get_agent_admin_service
                get_agent_admin_service().update_agent(ID, name=NAME)
                persona = Path(current.workspace) / "AGENT.md"
                text = persona.read_text(encoding="utf-8")
                persona.write_text(re.sub(rf"\b{re.escape(current.name)}\b", NAME, text), encoding="utf-8")
                logger.info(f"[Personal] renamed the personal agent {current.name} -> {NAME}")
            except Exception as e:
                logger.warning(f"[Personal] could not rename the personal agent: {e}")
        return False
    try:
        from agent.admin import get_agent_admin_service
        data = get_agent_admin_service().create_agent(
            ID, NAME, description="Your personal assistant: voice, camera, research and private memory.")
        ws = Path(data.get("workspace") or workspace())
        (ws / "AGENT.md").write_text(PERSONA.format(name=NAME), encoding="utf-8")
        (ws / "memory" / "personal").mkdir(parents=True, exist_ok=True)
        logger.info(f"[Personal] created the personal agent '{NAME}' at {ws}")
        return True
    except Exception as e:
        logger.warning(f"[Personal] could not create the personal agent: {e}")
        return False


def agent_of(executor):
    agent = getattr(executor, "agent", None)
    for value in (getattr(getattr(executor, "model", None), "agent_id", None), getattr(agent, "agent_id", None),
                  getattr(agent, "id", None)):
        if value:
            return str(value)
    return ""


_PATH_KEYS = ("path", "file_path", "file", "directory", "dir", "target", "image", "audio")


def refusal(executor, tool_name, arguments):
    """A refusal when an agent other than the personal one reaches into its workspace."""
    ws = workspace()
    if ws is None or agent_of(executor) == ID:
        return None
    root = os.path.realpath(ws)
    values = []
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and (key in _PATH_KEYS or key == "command"):
            values.append(value)
        elif isinstance(value, list) and key in ("paths", "files", "images"):
            values += [v for v in value if isinstance(v, str)]
    for raw in values:
        if raw.startswith(("http://", "https://")):
            continue
        if tool_name == "bash" or len(raw) > 400:
            if root in raw or re.search(r"agents/personal(/|\b)", raw):
                break
            continue
        path = raw[7:] if raw.startswith("file://") else raw
        if os.path.isabs(path) and os.path.realpath(path).startswith(root + os.sep) or os.path.realpath(path) == root:
            break
    else:
        return None
    return (f"That is {NAME}'s private space (the owner's personal assistant). Other agents can't open it. "
            f"If the owner wants something from there, they can ask {NAME}.")


PREFS_MARK = "<!-- lrm-prefs -->"
PREFS = PREFS_MARK + """
## Preferences (LRMagent)
- Dates: always DD-MM-YYYY, e.g. 04-11-2026 (times as 24-hour HH:MM).
- WhatsApp messages and reports: a short bold title line, then bullet points with the facts. No greetings,
  no filler, no closing lines. Only add a sentence when a point needs it.
- The vault is the source of truth: before relying on something you remember (memory, daily notes, chat
  summaries), check it against the vault (memory_search covers it; read the note). If memory and the vault
  disagree, the vault wins: it is what the owner approved. Say so when your memory was out of date.
- Finished results go to the vault only through vault_update (Lorium checks, the owner approves).
- Act, don't promise: when the owner asks for something to be set up, do it yourself in the same reply with
  your tools, then confirm what now exists. Anything at a time or on repeat (daily reports at 12:00 and 17:00,
  reminders, weekly summaries) is a scheduled task: create it with the scheduler tool (action 'create',
  schedule_type 'cron', timezone 'Asia/Jakarta', ai_task saying exactly what to do and report). Results go to
  the owner's WhatsApp: call scheduler action 'list_recipients' and use the 'You (owner)' WhatsApp entry
  (its channel_type, instance_id and receiver). Reply with the task name, the times and where it will arrive;
  it is listed in the console's Tasks page. Never say something is set up unless the tool call succeeded.
- A report that needs another team's data: ask that team's agent (CFO, HRD, Operations, ...) with ask_agent,
  all at once with parallel calls, instead of fetching their data yourself. Put their answers together and
  say plainly which part is missing and why.
- A report for a WhatsApp group: schedule it with that group as the recipient (scheduler 'list_recipients' shows the
  groups the owner opened to agents as "Group: <name>", with their channel_type, instance_id and receiver). In other
  work, post to an open group with the whatsapp tool (to: the group's name or id).
- A branch WhatsApp set to Monitor only: you never message anyone there (the gateway blocks it anyway). Report on it
  with chat_monitor (action 'report'); for a daily report, schedule an ai_task that runs chat_monitor and sends the
  owner the summary.
- A site asks you to sign in (or shows its login page): use site_login (action 'sign_in' with the address); the
  browser types the owner's saved login. Never ask for passwords in chat.
""" + PREFS_MARK + "\n"


def ensure_preferences():
    """The owner's standing preferences in every agent's owner profile (USER.md), kept current."""
    import re as _re
    try:
        for p in registry().list():
            path = Path(p.workspace) / "USER.md"
            try:
                text = path.read_text(encoding="utf-8") if path.exists() else ""
            except OSError:
                continue
            # replace an earlier block (and the first, date-only version) with the current one
            new = _re.sub(_re.escape(PREFS_MARK) + r".*?" + _re.escape(PREFS_MARK) + r"\n?", "", text, flags=_re.S)
            new = new.replace("\n## Preferences (LRMagent)\n- Dates: always write them as DD-MM-YYYY, e.g. 04-11-2026 (times as 24-hour HH:MM).\n", "")
            new = new.rstrip() + "\n\n" + PREFS
            if new != text:
                try:
                    path.write_text(new, encoding="utf-8")
                except OSError as e:
                    logger.warning(f"[Personal] could not update the preferences for {p.id}: {e}")
    except Exception as e:
        logger.warning(f"[Personal] preferences not applied: {e}")
