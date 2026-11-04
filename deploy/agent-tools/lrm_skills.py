"""Skill sources people copy from the web, turned into the GitHub installer's spec.

The Skills page installs from GitHub (repo URL, folder URL or owner/repo). Skill
directories publish install lines for the `skills` npm CLI instead, e.g.
``npx skills add vercel-labs/agent-skills --skill frontend-design`` or a skills.sh
page link. Those all point at a GitHub repository, so they install the same way
(nothing from npm is run on the server). The preview then lists the skills found
and the owner ticks the ones to keep.
"""
import re
import shlex

_REPO = re.compile(r"^[A-Za-z0-9_\-]+/[A-Za-z0-9_.\-]+$")
_NPX = re.compile(r"^(?:npx|pnpx|bunx|pnpm\s+dlx|yarn\s+dlx)\s+(?:-y\s+|--yes\s+)?(?:skills|add-skill)(?:@[\w.\-]+)?\s", re.I)
_SKILLS_SH = re.compile(r"^https?://(?:www\.)?skills\.sh/([A-Za-z0-9_\-]+)/([A-Za-z0-9_.\-]+)(?:/.*)?$", re.I)


def github_spec(value: str) -> str:
    """``value`` unchanged unless it is an npx skills command or a skills.sh link."""
    value = (value or "").strip()
    page = _SKILLS_SH.match(value)
    if page:
        return f"{page.group(1)}/{page.group(2)}"
    if not _NPX.match(value):
        return value
    try:
        words = shlex.split(value)
    except ValueError:
        raise ValueError("could not read that npx command")
    at = next(i for i, w in enumerate(words) if re.match(r"^(skills|add-skill)(@[\w.\-]+)?$", w, re.I))
    args, skip = [], False
    for w in words[at + 1:]:
        if skip:
            skip = False
        elif w in ("--skill", "-s", "--agent", "-a"):
            skip = True  # takes a value
        elif not w.startswith("-"):
            args.append(w)
    # `skills add <source>`; `add-skill <source>` has no sub-command.
    if args and args[0].lower() in ("add", "install", "i"):
        args = args[1:]
    if not args:
        raise ValueError("the npx command does not name a repository")
    source = args[0]
    if source.startswith(("github:", "gh:")):
        source = source.split(":", 1)[1]
    if _REPO.match(source.split("@", 1)[0].removesuffix(".git")):
        source = source.split("@", 1)[0]
    if source.startswith(("https://github.com/", "http://github.com/")) or _REPO.match(source.removesuffix(".git")):
        return source.removesuffix(".git")
    raise ValueError("only skills hosted on GitHub can be installed from an npx command")


def share_with_all(names, agent_id=None):
    """Make skills just installed for one agent available to every agent.

    Skills live in one shared folder (the default agent's workspace ``skills/``),
    but an agent can have its own folder or a "only these skills" list. Copy
    the new skills into every own folder and add them to every list.
    """
    import os
    import shutil
    from pathlib import Path

    from agent.admin import get_agent_admin_service
    from agent.registry import get_agent_registry
    from common import state_dir
    from common.log import logger

    names = [n for n in (names or []) if n]
    if not names:
        return
    registry = get_agent_registry()
    source = state_dir.skills_dir(base=registry.get(agent_id, require_enabled=False).workspace if agent_id
                                  else registry.get(require_enabled=False).workspace)
    targets = {Path(state_dir.shared_root()) / "skills"}
    for profile in registry.list():
        own = Path(profile.workspace) / "skills"
        if own.is_dir():
            targets.add(own)
    for target in targets:
        if os.path.realpath(target) == os.path.realpath(source):
            continue
        for name in names:
            src = Path(source) / name
            if src.is_dir():
                try:
                    shutil.copytree(src, target / name, dirs_exist_ok=True)
                except OSError as e:
                    logger.warning(f"[Skills] could not copy {name} to {target}: {e}")
    admin = get_agent_admin_service()
    for profile in registry.list():
        if profile.skills is None:
            continue  # uses every skill already
        missing = [n for n in names if n not in profile.skills]
        if missing:
            try:
                admin.update_agent(profile.id, skills=list(profile.skills) + missing)
            except Exception as e:
                logger.warning(f"[Skills] could not add {missing} to {profile.id}: {e}")
