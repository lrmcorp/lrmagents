"""What a scheduled agent run actually produced.

The scheduler used to deliver the agent's last message verbatim, so a run that
only thought out loud ("Let me finalize…") or produced nothing still sent that
text. This decides whether a run has a real final answer to send, something it
chose to stay silent about, or nothing usable (which must fail loudly, not ship
the model's notes). The final answer is taken from an explicit marker the run is
asked to wrap it in; without a marker, obvious thinking is rejected and clean
content passes through so reports that predate the marker still send.
"""

import re

FINAL_OPEN = "[KIRIM]"
FINAL_CLOSE = "[/KIRIM]"

# Appended to a scheduled run's prompt so the model marks the text to send.
INSTRUCTION = (
    "\n\nPENTING — cara mengirim hasil:\n"
    f"Tulis HANYA laporan/jawaban akhir untuk penerima di antara {FINAL_OPEN} dan "
    f"{FINAL_CLOSE}. Teks di luar penanda tidak dikirim, jadi taruh catatan proses, "
    "rencana, atau debug di luar. Bila tidak ada yang perlu dikirim, tulis "
    f"{FINAL_OPEN}{FINAL_CLOSE} kosong."
)

# English first-person planning that has leaked into reports. Indonesian business
# reports do not open lines this way, so these match stray thinking, not content.
_REASONING = re.compile(
    r"(?im)^\s*(let me\b|let'?s\b|i'?ll\b|i will\b|i need to\b|i should\b|"
    r"i'?m going to\b|first,? i\b|now i'?\b|next,? i\b|actually,|wait,|hmm\b|"
    r"okay,? (so|let|i)\b|that was a bad|confirmed\s*[—-]|i'?ve|let me call|"
    r"let me finalize|let me get|let me just|to be precise)")

# A run that hits an access or verification wall produces a coherent "report failed"
# message as its answer (e.g. an inter-agent call refused because the run was not taken
# as the owner's, or branch data that could not be read). That is not a report: it must
# not be delivered to the recipients as if it were one — the run is marked failed and only
# the owner is told. These phrases name the owner/console access model or an unverifiable
# source, not ordinary report content (a rejected transaction is not matched).
_FAILURE = re.compile(
    r"(?i)("
    r"akses[^.\n]{0,80}\bditolak|access[^.\n]{0,80}\bdenied|"
    r"\bditolak[^.\n]{0,80}(pemilik|owner|konsol|console|akun pemilik)|"
    r"tidak (di|ber)jalan(kan)?[^.\n]{0,50}(konsol|console|akun pemilik|chat[^.\n]{0,20}pemilik)|"
    r"tidak berasal dari[^.\n]{0,40}(konsol|akun|chat)[^.\n]{0,20}pemilik|"
    r"message yourself|chat whatsapp pemilik sendiri|"
    r"only the owner can use it|from the lrmagent console|"
    r"tidak dapat diverifikasi|could not be verified|cannot be verified|"
    r"laporan gagal dibuat|gagal dibuat karena akses"
    r")")


# The refusal LRMagent itself returns (lrm_owner.REFUSAL), quoted verbatim by a run.
_REFUSAL_TEXT = "so only the owner can use it: from the LRMagent console"


def _run_refused(session_id=None):
    """Did this scheduled run actually hit an owner-only refusal (recorded by lrm_owner)?"""
    try:
        try:
            from agent import lrm_task_runs
        except ImportError:
            import lrm_task_runs  # standalone tests
        return lrm_task_runs.was_refused(session_id)
    except Exception:
        return False


def _is_failure_report(text, refused=False):
    """True when the deliverable is itself a system access/verification failure, not a
    report. The wording alone is not enough ("cuti ditolak pemilik toko" is a real report):
    the run must have been refused by an owner-only check, or quote the refusal verbatim.
    Guarded so an incidental mention deep inside a real, long report does not trip it."""
    if _REFUSAL_TEXT in text:
        return True
    return bool(refused and _FAILURE.search(text) and (len(text) < 700 or _FAILURE.search(text[:250])))


_MARKED = re.compile(re.escape(FINAL_OPEN) + r"(.*?)(?:" + re.escape(FINAL_CLOSE) + r"|(?=" + re.escape(FINAL_OPEN)
                     + r")|\Z)", re.IGNORECASE | re.DOTALL)


def _between(text):
    """The last non-empty block the run wrapped in the final-answer marker (any case),
    "" when every marked block is empty, or None if unmarked."""
    blocks = [m.group(1).strip() for m in _MARKED.finditer(text)]
    if not blocks:
        return None
    return next((b for b in reversed(blocks) if b), "")


def classify(raw, session_id=None):
    """("final", payload) to send, ("silent", "") to send nothing on purpose, or
    ("failed", reason) when the run has no usable final answer. session_id is the
    scheduled run's; without it, the run this thread just finished is used."""
    text = (raw or "").strip()
    if not text:
        return ("failed", "the run produced no output")
    inner = _between(text)
    if inner is not None:
        if not inner:
            return ("silent", "")
        candidate = inner
    else:
        hits = len(_REASONING.findall(text))
        if hits >= 2 or (hits == 1 and len(text) < 400):
            return ("failed", "the run returned its thinking, not a final answer")
        candidate = text
    if _is_failure_report(candidate, _run_refused(session_id)):
        return ("failed", "the agent could not reach the data it needs (access denied or unverifiable)")
    return ("final", candidate)


def note_failed(agent_id, task, reason):
    """Tell the owner a scheduled run failed and sent nothing. Never raises."""
    try:
        from agent import lrm_activity
        name = lrm_activity._agent_name(agent_id)
        label = (task or {}).get("name") or (task or {}).get("id") or "a scheduled task"
        lrm_activity._notify_owner(
            agent_id,
            f"⚠️ Scheduled report failed ({name}): {reason}. Task: {label}. Nothing was sent.",
        )
    except Exception:
        pass
