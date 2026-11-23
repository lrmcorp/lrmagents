"""Stubbed tests for the vault flow's knowledge/ write gate, secret filter and Undo
(python3 test_lrm_vault_flow.py)."""

import os
import sys
import tempfile
import types
from pathlib import Path


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_mod("common")
_mod("common.log").logger = types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None)
_TMP = tempfile.mkdtemp()
_mod("config").get_data_root = lambda: _TMP
_mod("common.atomic_write").write_text_atomic = lambda path, text: Path(path).write_text(text, encoding="utf-8")
_mod("agent")
_reg = types.SimpleNamespace(default_agent_id="lorium",
                             get=lambda *a, **k: types.SimpleNamespace(id="lorium", name="Lorium"))
_mod("agent.registry").get_agent_registry = lambda: _reg
_mod("channel")
_mod("channel.web")
_mod("channel.web.api")
_VAULT_DIR = Path(_TMP) / "ws"


class _Vault:
    def __init__(self, agent_id):
        pass

    def resolve(self, vpath):
        return "knowledge", vpath, _VAULT_DIR / vpath

    def changed(self, sections, rels):
        pass


_mod("channel.web.api.lrm_vault").Vault = _Vault

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_vault_flow as vf  # noqa: E402

PASS = {"f": 0}


def check(name, got, want):
    if got != want:
        PASS["f"] += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


# Knowledge write gate: reads over knowledge/ pass, writes into it are refused.
ROOTS = ["/data/ws/knowledge"]
for cmd in ("grep -rn x knowledge/ 2>/dev/null", "cat knowledge/x.md > /tmp/x", "ls knowledge | head",
            "sed -n 1,20p knowledge/x.md", "cp knowledge/x.md /tmp/x", "cd knowledge && git log --oneline",
            "git -C knowledge status", "python3 -c \"print(open('knowledge/x.md').read())\"",
            "cd /tmp && echo x > knowledge/x.md"):
    check(f"read allowed: {cmd}", vf.shell_writes_knowledge(cmd, ROOTS), False)
for cmd in ("cd knowledge && echo x >> note.md", "python3 -c \"open('knowledge/x.md','w')\"",
            "echo x | tee knowledge/x.md", "echo x | tee -a knowledge/x.md", "sed -i 's/a/b/' knowledge/x.md",
            "mv knowledge/x.md /tmp/", "mv /tmp/x.md knowledge/", "cp /tmp/x.md knowledge/x.md", "rm knowledge/x.md",
            "rm -rf knowledge", "cd knowledge && git commit -am x", "git -C knowledge reset --hard",
            "git checkout knowledge/x.md", "echo x > /data/ws/knowledge/x.md", "echo x>knowledge/x.md",
            "cat > knowledge/x.md <<EOF\nhi\nEOF", "bash -c 'echo x > knowledge/x.md'",
            "find knowledge -name '*.md' -delete", "python3 - <<EOF\nopen('knowledge/x.md','w').write('x')\nEOF"):
    check(f"write refused: {cmd[:40]}", vf.shell_writes_knowledge(cmd, ROOTS), True)


def luhn_complete(prefix):
    """prefix + the check digit that makes it Luhn-valid (the worst case for a phone number)."""
    return next(prefix + str(d) for d in range(10) if vf._luhn(prefix + str(d)))


# Secret filter: phone numbers and ordinary labelled words pass; real secrets are refused.
phone62, phone08 = luhn_complete("62812345678901"), luhn_complete("0812345678901")
for text in (f"Hubungi Budi di {phone62}", f"WA: +{phone62}", f"HP gudang {phone08}",
             f"+62 {phone62[2:5]}-{phone62[5:9]}-{phone62[9:]}", f"cc Pak Budi {phone62}",
             f"Kartu stok: hubungi admin gudang di nomor {phone62}",
             "Token: 500 kWh", "token listrik: 20 kWh", "pin: lokasi gudang", "Password: lihat di brankas pemilik",
             "Sandi: tanya Budi"):
    check(f"allowed: {text}", vf.secret_in(text), None)
for text, kind in (("kartu kredit 4111 1111 1111 1111", "a card number"), ("4111111111111111", "a card number"),
                   ("no 5500005555555559", "a card number"), (f"no kartu: {luhn_complete('621234567890123')}", "a card number"),
                   ("4111-1111-1111-1111", "a card number"), ("PIN: 4821", "a password, PIN or key"),
                   ("password: Rahasia123!", "a password, PIN or key"), ("Password: admin", "a password, PIN or key"),
                   ("api_key = a8f3k2l9q0w7e6r5x", "a password, PIN or key"),
                   ("token: abcdefgh12345678", "a password, PIN or key"), ("otp: 739201", "a password, PIN or key"),
                   ("ghp_" + "a" * 36, "an API key or token")):
    check(f"refused: {text[:30]}", vf.secret_in(text), kind)

# Undo: a small correction saved on its own goes back, unless the note changed since.
_reg.get = lambda *a, **k: types.SimpleNamespace(id="lorium", name="Lorium")
note = _VAULT_DIR / "knowledge/ops/SOP.md"
note.parent.mkdir(parents=True, exist_ok=True)
base = "\n".join(f"Langkah {i}: cek stok." for i in range(20)) + "\n"


def saved_correction(item_id):
    note.write_text(base, encoding="utf-8")
    items = vf._load()
    items.append({"id": item_id, "status": "review", "path": "knowledge/ops/SOP.md", "title": "SOP",
                  "content": base.replace("Langkah 3: cek stok.", "Langkah 3: cek stok dua kali."),
                  "summary": "langkah 3", "agent": "ops", "history": [], "updated": 0})
    vf._save(items)
    return vf.review(item_id, "lorium", "approve")


item = saved_correction("a1")
check("correction saved on its own", (item["status"], item.get("auto")), ("applied", True))
check("undo restores the note", (vf.revert("a1")["status"], note.read_text(encoding="utf-8")), ("reverted", base))
saved_correction("a2")
note.write_text(note.read_text(encoding="utf-8") + "\nCatatan pemilik.\n", encoding="utf-8")
try:
    vf.revert("a2")
    check("undo after an edit refused", "reverted", "refused")
except ValueError as e:
    check("undo after an edit refused", "has changed since" in str(e), True)
check("edited note kept", "Catatan pemilik." in note.read_text(encoding="utf-8"), True)

print("FAILED" if PASS["f"] else "ALL PASSED")
sys.exit(1 if PASS["f"] else 0)
