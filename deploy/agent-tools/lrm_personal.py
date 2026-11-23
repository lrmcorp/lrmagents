"""The owner's personal assistant: a separate agent with voice, camera and private memory.

- Created once at start-up (id "personal", named Sora; rename it on the Agents page).
- Its memory is its own workspace. Other agents can't open files there (file tools and
  shell commands that name it are refused), so what is personal stays with it.
- Chats any agent has that are about the owner's private life are summarised into its
  memory, not the other agent's (agent/lrm_memory.py asks the model to classify).
- It can ask the other agents for reports (ask_agent tool), and is reached by voice on
  the console's Personal page (orb), as well as in normal chat.
"""
import fnmatch
import glob
import itertools
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
_WALK_TOOLS = ("search_files",)  # file tools that read a whole tree: an ancestor folder reaches her files
_GLOB = re.compile(r"[*?\[{]")
_WALKERS = {"rg", "ag", "ack", "find", "tree", "tar", "zip", "rsync", "du", "rgrep", "locate", "fd", "fdfind", "7z"}
_WALK_WITH_FLAG = {"grep", "egrep", "fgrep", "zgrep", "ls", "cp", "scp", "chmod", "chown", "rm", "mv"}
_RECURSIVE_FLAG = re.compile(r"^-(-recursive|-dereference-recursive|[A-Za-z]*[rRa][A-Za-z]*)$")


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _braces(word, limit=64):
    """Simple shell brace expansion ({a,b}), so 'agents/{personal,x}' is checked like the shell sees it."""
    out, todo = [], [word]
    while todo and len(out) < limit:
        w = todo.pop()
        m = re.search(r"\{([^{}]*,[^{}]*)\}", w)
        if not m:
            out.append(w)
            continue
        todo += [w[:m.start()] + part + w[m.end():] for part in m.group(1).split(",")]
    return out


def resolve(path, cwd):
    """A tool's path argument as the file tools resolve it: ~ expanded, relative to the working dir."""
    path = path[7:] if path.startswith("file://") else path
    path = os.path.expanduser(path)
    return os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path))


def bash_reaches(command, cwd, protected):
    """True when a shell command could read inside any ``protected`` folder (real paths): by naming it,
    by a glob or a cd that lands there, or by a recursive walk (grep -r, find, tar, ...) over a folder
    above it. Best effort and on the side of refusing; a determined script can still get round it."""
    command = str(command or "")
    protected = [os.path.realpath(p) for p in protected if p]
    if not protected:
        return False
    if any(p in command for p in protected):
        return True
    words = [w for w in re.split(r"[\s;&|()<>`'\"=]+", command) if w]
    names = {os.path.basename(p) for p in protected}
    parents = {os.path.basename(os.path.dirname(p)) for p in protected}
    walker = any(os.path.basename(w) in _WALKERS for w in words) or (
        any(os.path.basename(w) in _WALK_WITH_FLAG for w in words) and any(_RECURSIVE_FLAG.match(w) for w in words))
    bases, explicit_dir = [os.path.realpath(cwd or os.getcwd())], False
    for i, word in enumerate(words):
        if i and words[i - 1] in ("cd", "pushd") and not word.startswith("-"):
            bases.append(resolve(os.path.expandvars(word), bases[-1]))
    for word in words:
        if word.startswith("-") or "://" in word:
            continue
        for variant in _braces(os.path.expandvars(word)):
            segments = [s for s in variant.split("/") if s]
            for i, seg in enumerate(segments):
                if not _GLOB.search(seg):
                    continue
                # 'pers*' / 'person?l' could be her folder; 'agents/*' covers every agent's folder.
                if re.search(r"[A-Za-z0-9]", seg) and any(fnmatch.fnmatchcase(n, seg) for n in names):
                    return True
                if i and segments[i - 1] in parents:
                    return True
            for base in bases:
                full = os.path.normpath(os.path.join(base, os.path.expanduser(variant)))
                hits = itertools.islice(glob.iglob(full), 2000) if _GLOB.search(variant) else [full]
                for hit in hits:
                    real = os.path.realpath(hit)
                    if any(_inside(real, p) for p in protected):
                        return True
                    if walker and os.path.isdir(real):
                        explicit_dir = True
                        if any(_inside(p, real) for p in protected):
                            return True
    # grep -r / find / rg with no folder named walk the current one
    return walker and not explicit_dir and any(_inside(p, bases[-1]) for p in protected)


def _cwd(executor):
    try:
        return executor.agent.effective_cwd()
    except Exception:
        try:
            return registry().get(agent_of(executor), require_enabled=False).workspace
        except Exception:
            return os.getcwd()


def refusal(executor, tool_name, arguments):
    """A refusal when an agent other than the personal one reaches into its workspace."""
    ws = workspace()
    if ws is None or agent_of(executor) == ID:
        return None
    root = os.path.realpath(os.path.expanduser(str(ws)))
    cwd = _cwd(executor)
    arguments = arguments or {}
    values = []
    for key, value in arguments.items():
        if isinstance(value, str) and key in _PATH_KEYS:
            values.append(value)
        elif isinstance(value, list) and key in ("paths", "files", "images"):
            values += [v for v in value if isinstance(v, str)]
    if tool_name in _WALK_TOOLS and not any(isinstance(arguments.get(k), str) and arguments.get(k) for k in _PATH_KEYS):
        values.append(".")  # search_files defaults to the workspace root
    blocked = tool_name == "bash" and bash_reaches(arguments.get("command"), cwd, [root, str(ws)])
    walked = False
    for raw in values:
        if blocked or raw.startswith(("http://", "https://")):
            continue
        full = resolve(raw, cwd)
        if _inside(full, root):
            blocked = True
        elif tool_name in _WALK_TOOLS and _inside(root, full):
            blocked = walked = True
    if not blocked:
        return None
    if walked:
        return (f"That folder contains {NAME}'s private space (the owner's personal assistant), so it can't be searched "
                "as a whole. Search a folder inside your own workspace instead (for example memory/ or knowledge/).")
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
- Branch WhatsApp numbers set to Monitor only: nobody ever messages anyone there (the gateway blocks it anyway).
  Keep these reports cheap: use chat_monitor 'digest' / 'digest_all' (counted by code, short excerpts), never paste
  whole conversations; 'report' only when the owner asks for detail. The chain:
  - Operations: once a day, chat_monitor 'digest_all', then (1) post the per-branch report (one short section per
    branch) to the HR group with the whatsapp tool, if the owner opened one, and (2) ask_agent Lorium with only what
    needs attention (complaints, unanswered customers, slow replies, lost sales) - or "nothing to flag".
  - Lorium: tells the owner on WhatsApp only when something needs them; otherwise it goes in the next daily report.
  - Branch agents: their own branch with 'digest' when asked.
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
