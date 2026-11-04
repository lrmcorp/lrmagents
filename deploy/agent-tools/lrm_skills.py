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
