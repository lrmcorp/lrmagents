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


def _between(text):
    """The text the run wrapped in the final-answer marker, or None if unmarked."""
    open_at = text.rfind(FINAL_OPEN)
    if open_at == -1:
        return None
    rest = text[open_at + len(FINAL_OPEN):]
    close_at = rest.find(FINAL_CLOSE)
    return (rest if close_at == -1 else rest[:close_at]).strip()


def classify(raw):
    """("final", payload) to send, ("silent", "") to send nothing on purpose, or
    ("failed", reason) when the run has no usable final answer."""
    text = (raw or "").strip()
    if not text:
        return ("failed", "the run produced no output")
    inner = _between(text)
    if inner is not None:
        return ("final", inner) if inner else ("silent", "")
    hits = len(_REASONING.findall(text))
    if hits >= 2 or (hits == 1 and len(text) < 400):
        return ("failed", "the run returned its thinking, not a final answer")
    return ("final", text)


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
