"""The Vault page's API, /api/lrm/vault (console login required).

Each agent's memory is shown as one Obsidian-style vault, laid over the files
the agent already reads and writes:

    MEMORY.md     -> <workspace>/MEMORY.md
    memory/...    -> <workspace>/memory/...  (daily notes, dreams, ...)
    knowledge/... -> the agent's knowledge dir (its own, else the shared one)

Knowledge changes keep knowledge/index.md and the search index in step through
KnowledgeService; memory changes mark the agent's memory index stale, exactly
like the console's own file editor. Deleted files go to <workspace>/.trash/
rather than being removed. Installed as channel/web/api/lrm_vault.py by the
LRMagent image.
"""

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import web

from channel.web.core._common import _require_auth
from common.log import logger

SECTIONS = ("memory", "knowledge")
ROOT_FILE = "MEMORY.md"
ASSET_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".pdf": "application/pdf",
}
# knowledge/index.md is regenerated and log.md is the agent's change log.
READ_ONLY = {"knowledge/index.md", "knowledge/log.md"}
BAD_NAME = re.compile(r'[<>:"|?*\\\x00-\x1f]')
MAX_NOTE = 2 * 1024 * 1024
MAX_ENTRIES = 5000

FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
WIKILINK = re.compile(r"!?\[\[([^\]|#^\n]*)([#^][^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
MDLINK = re.compile(r"!?\[[^\]\n]*\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
TAG = re.compile(r"(?:^|(?<=[\s(]))#([A-Za-z_][\w/-]*)")
HEADING = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)

_lock = threading.Lock()
_meta_cache = {}


class VaultError(ValueError):
    pass


# ---- locating files ---------------------------------------------------------

def _workspace(agent_id):
    from agent.registry import get_agent_registry
    registry = get_agent_registry()
    try:
        profile = registry.get(agent_id, require_enabled=False) if agent_id else registry.get(require_enabled=False)
    except KeyError:
        raise VaultError("unknown agent")
    return Path(profile.workspace)


class Vault:
    def __init__(self, agent_id):
        from common import state_dir
        self.agent_id = agent_id or None
        self.ws = _workspace(self.agent_id)
        self.bases = {
            "memory": self.ws / "memory",
            "knowledge": Path(state_dir.knowledge_dir(base=str(self.ws))),
        }

    def resolve(self, vpath, *, allow_section_root=False):
        """(section, rel, full path) for a vault path; refuses anything outside the vault."""
        if not isinstance(vpath, str) or not vpath.strip():
            raise VaultError("path is required")
        parts = vpath.strip().strip("/").split("/")
        if any(not p or p in (".", "..") or p.startswith(".") or BAD_NAME.search(p) for p in parts):
            raise VaultError("invalid name: use letters, numbers, spaces and - _ ( ) . only")
        if parts == [ROOT_FILE]:
            return "root", ROOT_FILE, self.ws / ROOT_FILE
        section = parts[0]
        if section not in SECTIONS:
            raise VaultError("path must be in memory/ or knowledge/")
        rel = "/".join(parts[1:])
        if not rel and not allow_section_root:
            raise VaultError("the memory and knowledge folders can't be changed")
        base = self.bases[section]
        full = base.joinpath(*parts[1:])
        try:
            full.resolve().relative_to(base.resolve())
        except ValueError:
            raise VaultError("path outside the vault")
        return section, rel, full

    def vpath(self, section, rel):
        return ROOT_FILE if section == "root" else f"{section}/{rel}".rstrip("/")

    # ---- listing --------------------------------------------------------

    def entries(self):
        out = []
        root_file = self.ws / ROOT_FILE
        if root_file.is_file():
            st = root_file.stat()
            out.append({"path": ROOT_FILE, "type": "file", "mtime": st.st_mtime, "size": st.st_size})
        for section in SECTIONS:
            base = self.bases[section]
            out.append({"path": section, "type": "dir"})
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                reldir = os.path.relpath(dirpath, base).replace(os.sep, "/")
                prefix = section if reldir == "." else f"{section}/{reldir}"
                for d in dirnames:
                    out.append({"path": f"{prefix}/{d}", "type": "dir"})
                for name in sorted(filenames):
                    ext = os.path.splitext(name)[1].lower()
                    if name.startswith(".") or (ext != ".md" and ext not in ASSET_TYPES):
                        continue
                    try:
                        st = os.stat(os.path.join(dirpath, name))
                    except OSError:
                        continue
                    out.append({"path": f"{prefix}/{name}", "type": "file", "mtime": st.st_mtime, "size": st.st_size})
                if len(out) > MAX_ENTRIES:
                    return out[:MAX_ENTRIES], True
        return out, False

    def notes(self, entries):
        return [e for e in entries if e["type"] == "file" and e["path"].lower().endswith(".md")]

    def safe(self, vpath):
        """Full path of a listed file, or None for one that points outside the vault (a symlink)."""
        try:
            return self.resolve(vpath)[2]
        except VaultError:
            return None

    def read_text(self, vpath):
        _, _, full = self.resolve(vpath)
        if not full.is_file():
            raise FileNotFoundError("note not found")
        if full.stat().st_size > MAX_NOTE:
            raise VaultError("note too large to open here")
        return full.read_text(encoding="utf-8", errors="replace"), full.stat().st_mtime

    # ---- writing --------------------------------------------------------

    def knowledge(self):
        from agent.knowledge.service import KnowledgeService
        return KnowledgeService(str(self.ws), reindex_in_background=True)

    def memory_changed(self):
        from channel.web.api.workspace import _mark_memory_dirty
        _mark_memory_dirty(self.agent_id)

    def knowledge_changed(self, paths=(), renamed=None):
        svc = self.knowledge()
        svc.rebuild_index_md(renamed=renamed)
        svc._reindex(list(paths), force=True)

    def changed(self, sections, knowledge_paths=(), renamed=None):
        if "knowledge" in sections:
            self.knowledge_changed(knowledge_paths, renamed)
        if sections & {"memory", "root"}:
            self.memory_changed()


def _atomic_write(full, text):
    from common.atomic_write import write_text_atomic
    full.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(full, text)


# ---- note metadata (links, tags, properties) for graph, backlinks and tags ------

def _frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    props, key = {}, None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        item = re.match(r"^\s+-\s*(.*)$", line)
        if item and key:
            props.setdefault(key, [])
            if isinstance(props[key], list):
                props[key].append(item.group(1).strip().strip("'\""))
            continue
        kv = re.match(r"^([\w-]+)\s*:\s*(.*)$", line)
        if kv:
            key, value = kv.group(1).lower(), kv.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                props[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            else:
                props[key] = value.strip("'\"") if value else []
    rest = text[end + 4:]
    return props, rest[1:] if rest.startswith("\n") else rest


def _as_list(value):
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def note_meta(text):
    props, body = _frontmatter(text)
    plain = INLINE_CODE.sub("", FENCE.sub("", body))
    links = []
    for m in WIKILINK.finditer(plain):
        links.append({"t": m.group(1).strip(), "w": 1, "e": m.group(0).startswith("!")})
    for m in MDLINK.finditer(plain):
        href = m.group(1)
        if "://" in href or href.startswith(("#", "mailto:")):
            continue
        links.append({"t": href.split("#")[0], "w": 0, "e": m.group(0).startswith("!")})
    tags = {t.lstrip("#") for t in _as_list(props.get("tags") or props.get("tag"))}
    tags.update(m.group(1) for m in TAG.finditer(plain))
    heading = HEADING.search(plain)
    return {
        "title": heading.group(1).strip() if heading else "",
        "aliases": _as_list(props.get("aliases") or props.get("alias")),
        "tags": sorted(t for t in tags if t and not t.isdigit()),
        "links": [l for l in links if l["t"]],
    }


def _cached_meta(full, mtime, size):
    key = str(full)
    hit = _meta_cache.get(key)
    if hit and hit[0] == mtime and hit[1] == size:
        return hit[2]
    try:
        text = full.read_text(encoding="utf-8", errors="replace") if size <= MAX_NOTE else ""
    except OSError:
        text = ""
    meta = note_meta(text)
    if len(_meta_cache) > 20000:
        _meta_cache.clear()
    _meta_cache[key] = (mtime, size, meta)
    return meta


# ---- renames: keep links pointing at the moved note -------------------------------

def _stem(vpath):
    return os.path.splitext(vpath.rsplit("/", 1)[-1])[0]


def _relative(from_vpath, to_vpath):
    return os.path.relpath(to_vpath, os.path.dirname(from_vpath) or ".").replace(os.sep, "/")


def rewrite_links(text, note_vpath, old, new, bare_name_unique):
    """Text with links to vault note ``old`` pointed at ``new`` (both vault paths)."""
    old_noext, new_noext = old[:-3], new[:-3]
    old_targets = {old_noext.lower(), old.lower()}
    for section in SECTIONS:  # knowledge/a/b -> a/b, as written inside knowledge/
        if old_noext.startswith(section + "/"):
            old_targets.add(old_noext[len(section) + 1:].lower())
    if bare_name_unique:
        old_targets.add(_stem(old).lower())

    def wiki(m):
        target = m.group(1).strip()
        if target.lower().removesuffix(".md") not in {t.removesuffix(".md") for t in old_targets}:
            return m.group(0)
        if "/" not in target and bare_name_unique:
            replacement = _stem(new)
        elif target.lower().startswith(SECTIONS) or old_noext.split("/", 1)[0] != new_noext.split("/", 1)[0]:
            replacement = new_noext
        else:  # written relative to its section (knowledge/a/b as a/b): keep that style
            replacement = new_noext.split("/", 1)[1]
        return m.group(0).replace(m.group(1), replacement, 1)

    def md(m):
        href = m.group(1)
        if "://" in href or href.startswith(("#", "mailto:")):
            return m.group(0)
        path, _, anchor = href.partition("#")
        joined = os.path.normpath(os.path.join(os.path.dirname(note_vpath), path)).replace(os.sep, "/")
        if joined != old:
            return m.group(0)
        target = _relative(note_vpath, new) + (f"#{anchor}" if anchor else "")
        return m.group(0).replace(href, target, 1)

    return MDLINK.sub(md, WIKILINK.sub(wiki, text))


# ---- the handler ------------------------------------------------------------------

def _ok(**data):
    return json.dumps({"status": "success", **data}, ensure_ascii=False)


def _fail(message, code=None):
    data = {"status": "error", "message": str(message)}
    if code:
        data["code"] = code
    return json.dumps(data, ensure_ascii=False)


class LrmVaultHandler:
    def GET(self):
        _require_auth()
        params = web.input(op="index", agent_id="", path="", q="")
        try:
            vault = Vault(params.agent_id)
            if params.op == "file":
                return self._file(vault, params.path)
            web.header("Content-Type", "application/json; charset=utf-8")
            if params.op == "agents":
                return self._agents()
            if params.op == "index":
                return self._index(vault)
            if params.op == "read":
                section, rel, _ = vault.resolve(params.path)
                text, mtime = vault.read_text(params.path)
                return _ok(path=params.path, content=text, mtime=mtime,
                           editable=vault.vpath(section, rel) not in READ_ONLY)
            if params.op == "search":
                return self._search(vault, params.q)
            return _fail("unknown op")
        except (VaultError, FileNotFoundError) as e:
            web.header("Content-Type", "application/json; charset=utf-8")
            return _fail(e)
        except Exception as e:
            logger.error(f"[Vault] {params.op} failed: {e}")
            web.header("Content-Type", "application/json; charset=utf-8")
            return _fail(e)

    def POST(self):
        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(web.data() or b"{}")
            agent_id = body.get("agent_id") or web.input(agent_id="").agent_id
            vault = Vault(agent_id)
            op = body.get("op")
            with _lock:
                if op == "write":
                    return self._write(vault, body)
                if op == "create":
                    return self._create(vault, body)
                if op == "rename":
                    return self._rename(vault, body)
                if op == "delete":
                    return self._delete(vault, body)
            return _fail("unknown op")
        except (VaultError, FileNotFoundError, FileExistsError) as e:
            return _fail(e)
        except Exception as e:
            logger.error(f"[Vault] write failed: {e}")
            return _fail(e)

    # ---- reads ----------------------------------------------------------

    @staticmethod
    def _agents():
        from channel.web.api.agents import _agent_admin_service
        snap = _agent_admin_service().snapshot()
        agents = [{"id": a["id"], "name": a.get("name") or a["id"], "enabled": a.get("enabled", True),
                   "knowledge": a.get("knowledge_mode", "shared")} for a in snap.get("agents", [])]
        return _ok(agents=agents, default=snap.get("default_agent_id"))

    @staticmethod
    def _index(vault):
        entries, truncated = vault.entries()
        entries = [e for e in entries if e["type"] == "dir" or vault.safe(e["path"])]
        for e in vault.notes(entries):
            e.update(_cached_meta(vault.safe(e["path"]), e["mtime"], e["size"]))
            if e["path"] in READ_ONLY:
                e["readonly"] = True
        own = vault.ws / "knowledge"
        shared = own.is_symlink() or vault.bases["knowledge"].resolve() != own.resolve()
        return _ok(entries=entries, truncated=truncated, shared_knowledge=shared)

    @staticmethod
    def _file(vault, vpath):
        _, _, full = vault.resolve(vpath)
        kind = ASSET_TYPES.get(full.suffix.lower())
        if not kind or not full.is_file():
            raise FileNotFoundError("file not found")
        web.header("Content-Type", kind)
        web.header("X-Content-Type-Options", "nosniff")
        web.header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'")
        web.header("Cache-Control", "private, max-age=60")
        return full.read_bytes()

    @staticmethod
    def _search(vault, query):
        terms = [t.lower() for t in str(query or "").split() if t.strip()]
        if not terms:
            return _ok(results=[])
        results = []
        entries, _ = vault.entries()
        for e in vault.notes(entries):
            full = vault.safe(e["path"])
            if full is None or e["size"] > MAX_NOTE:
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            haystack = (e["path"] + "\n" + text).lower()
            if not all(t in haystack for t in terms):
                continue
            lines = text.splitlines()
            hits = []
            for no, line in enumerate(lines):
                if any(t in line.lower() for t in terms):
                    hits.append({"line": no + 1, "text": line.strip()[:240]})
                    if len(hits) == 3:
                        break
            score = sum(haystack.count(t) for t in terms) + (5 if any(t in e["path"].lower() for t in terms) else 0)
            results.append({"path": e["path"], "mtime": e["mtime"], "hits": hits, "score": score})
        results.sort(key=lambda r: (-r["score"], -r["mtime"]))
        return _ok(results=results[:50], total=len(results))

    # ---- writes ---------------------------------------------------------

    @staticmethod
    def _write(vault, body):
        vpath = body.get("path") or ""
        content = body.get("content")
        if not isinstance(content, str):
            raise VaultError("content must be text")
        section, rel, full = vault.resolve(vpath)
        if vault.vpath(section, rel) in READ_ONLY:
            raise VaultError("this note is maintained by the agent and is read-only")
        if not vpath.lower().endswith(".md"):
            raise VaultError("only notes can be edited")
        if len(content.encode("utf-8")) > MAX_NOTE:
            raise VaultError("note too large")
        expected = body.get("expected_mtime")
        from agent.workspace.service import WorkspaceConflictError
        try:
            if section == "knowledge":
                result = vault.knowledge().update_document(rel, content, expected_mtime=expected)
                return _ok(path=vpath, mtime=result["mtime"])
            from agent.workspace.service import WorkspaceService
            ws_rel = ROOT_FILE if section == "root" else f"memory/{rel}"
            result = WorkspaceService(str(vault.ws)).write_text(ws_rel, content, expected_mtime=expected)
        except WorkspaceConflictError:
            return _fail("This note changed on disk (probably the agent edited it) since you opened it.", "conflict")
        vault.memory_changed()
        return _ok(path=vpath, mtime=result["mtime"])

    @staticmethod
    def _create(vault, body):
        vpath = (body.get("path") or "").strip().strip("/")
        kind = body.get("kind") or "note"
        section, rel, full = vault.resolve(vpath)
        if full.exists():
            raise FileExistsError(f"'{vpath}' already exists")
        if kind == "folder":
            if section == "root":
                raise VaultError("invalid folder")
            full.mkdir(parents=True)
            return _ok(path=vpath)
        if not vpath.lower().endswith(".md"):
            raise VaultError("note names end with .md")
        content = body.get("content") or ""
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_NOTE:
            raise VaultError("invalid content")
        _atomic_write(full, content)
        vault.changed({section}, [rel])
        return _ok(path=vpath, mtime=full.stat().st_mtime)

    @staticmethod
    def _rename(vault, body):
        old, new = (body.get("path") or "").strip("/"), (body.get("new_path") or "").strip("/")
        s_old, r_old, f_old = vault.resolve(old)
        s_new, r_new, f_new = vault.resolve(new)
        if s_old == "root" or s_new == "root":
            raise VaultError("MEMORY.md can't be renamed: the agent reads it by that name")
        if old in READ_ONLY:
            raise VaultError("this note is maintained by the agent")
        if not f_old.exists():
            raise FileNotFoundError(f"'{old}' not found")
        if f_new.exists() and f_new.resolve() != f_old.resolve():
            raise FileExistsError(f"'{new}' already exists")
        is_dir = f_old.is_dir()
        if is_dir and (new + "/").startswith(old + "/"):
            raise VaultError("can't move a folder into itself")
        if not is_dir and old.lower().endswith(".md") != new.lower().endswith(".md"):
            raise VaultError("keep the .md extension")
        entries, _ = vault.entries()
        moved_notes = {}
        if is_dir:
            for e in vault.notes(entries):
                if e["path"].startswith(old + "/"):
                    moved_notes[e["path"]] = new + e["path"][len(old):]
        elif old.lower().endswith(".md"):
            moved_notes[old] = new
        unique = {o: sum(1 for e in vault.notes(entries) if _stem(e["path"]).lower() == _stem(o).lower()) == 1
                  for o in moved_notes}

        f_new.parent.mkdir(parents=True, exist_ok=True)
        if s_old == s_new:
            os.rename(f_old, f_new)
        else:
            shutil.move(str(f_old), str(f_new))
        touched = {s_old, s_new}
        in_knowledge = lambda p: p[len("knowledge/"):] if p.startswith("knowledge/") else None
        knowledge_paths = [in_knowledge(o) for o in moved_notes if in_knowledge(o)]
        if s_old == "knowledge" and not is_dir:
            knowledge_paths.append(r_old)
        # index.md keeps each moved page's summary when told where it went.
        renamed = {in_knowledge(o): in_knowledge(n) for o, n in moved_notes.items()
                   if in_knowledge(o) and in_knowledge(n)} or None

        updated = 0
        if body.get("update_links", True) and moved_notes:
            new_entries, _ = vault.entries()
            for e in vault.notes(new_entries):
                if e["path"] in READ_ONLY:
                    continue
                if vault.safe(e["path"]) is None:
                    continue
                section, rel, full = vault.resolve(e["path"])
                try:
                    text = full.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                # A moved note's own relative links are resolved from where it was.
                source = next((o for o, n in moved_notes.items() if n == e["path"]), e["path"])
                result = text
                for o, n in moved_notes.items():
                    result = rewrite_links(result, source, o, n, unique[o])
                if source != e["path"]:
                    result = _rebase_relative_links(result, source, e["path"], moved_notes)
                if result != text:
                    _atomic_write(full, result)
                    touched.add(section)
                    if section == "knowledge":
                        knowledge_paths.append(rel)
                    updated += 1
        vault.changed(touched, knowledge_paths, renamed)
        return _ok(path=new, updated_links=updated)

    @staticmethod
    def _delete(vault, body):
        vpath = (body.get("path") or "").strip("/")
        section, rel, full = vault.resolve(vpath)
        if section == "root":
            raise VaultError("MEMORY.md can't be deleted: empty it instead")
        if vpath in READ_ONLY:
            raise VaultError("this note is maintained by the agent")
        if not full.exists():
            raise FileNotFoundError(f"'{vpath}' not found")
        trash = vault.ws / ".trash" / time.strftime("%Y%m%d-%H%M%S") / vpath
        trash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(full), str(trash))
        vault.changed({section}, [rel])
        return _ok(path=vpath, trash=str(trash.relative_to(vault.ws)))


def _rebase_relative_links(text, old_vpath, new_vpath, moved):
    """A moved note's relative Markdown links, rewritten to work from its new folder."""
    if os.path.dirname(old_vpath) == os.path.dirname(new_vpath):
        return text

    def md(m):
        href = m.group(1)
        if "://" in href or href.startswith(("#", "mailto:", "/")):
            return m.group(0)
        path, _, anchor = href.partition("#")
        target = os.path.normpath(os.path.join(os.path.dirname(old_vpath), path)).replace(os.sep, "/")
        target = moved.get(target, target)
        rebased = _relative(new_vpath, target) + (f"#{anchor}" if anchor else "")
        return m.group(0).replace(href, rebased, 1)

    return MDLINK.sub(md, text)
