"""Standalone tests for the scheduled-run output classifier (python3 test_lrm_task_output.py)."""

import os
import sys
import types

sys.modules.setdefault("common", types.ModuleType("common"))
_log = types.ModuleType("common.log")
_log.logger = types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None)
sys.modules.setdefault("common.log", _log)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_task_output as out  # noqa: E402
import lrm_task_runs as runs  # noqa: E402


def run(raw, refused):
    """Classify raw as a scheduled run that did / did not hit an owner-only refusal."""
    sid = "scheduler_r1_t1"
    runs.start(sid, {"id": "t1", "name": "Laporan"})
    if refused:
        runs.note_refusal(sid)
    runs.finish(sid)
    return out.classify(raw)


def check(name, got, want):
    if got != want:
        print(f"FAIL {name}: got {got!r}, want {want!r}")
        return 1
    print(f"ok   {name}")
    return 0


def main():
    fails = 0
    M = out.FINAL_OPEN + "{}" + out.FINAL_CLOSE

    # A marked final answer is sent, markers stripped.
    fails += check("marked", out.classify("thinking...\n" + M.format("Laporan HRD: 9/16 hadir.")),
                   ("final", "Laporan HRD: 9/16 hadir."))
    # An empty marker is a deliberate "nothing to send".
    fails += check("empty marker", out.classify("looked, all clear " + M.format("")), ("silent", ""))
    # No output at all is a failure, not a silent done.
    fails += check("blank", out.classify("   "), ("failed", "the run produced no output"))
    fails += check("none", out.classify(""), ("failed", "the run produced no output"))
    # Leaked thinking without a marker is a failure.
    reasoning = ("Let me finalize the report text and get precise cutoff time.\n"
                 "Actually, I'll just note cutoff as 09:00 WIB. I'll call time.")
    fails += check("reasoning", out.classify(reasoning)[0], "failed")
    fails += check("short confirm", out.classify("Confirmed — the page redirects to the dashboard.")[0], "failed")
    # A clean unmarked report (predates the marker) still goes through.
    clean = "Laporan Operasional 7 Nov:\nStok masuk 12 nota, keluar 8. Semua sudah diproses."
    fails += check("clean unmarked", out.classify(clean), ("final", clean))

    # A system access/verification failure is NOT delivered as a report: it fails, but only
    # when the run really was refused by an owner-only check.
    denied = ("Laporan gagal dibuat: akses untuk meminta data ke LRM - Operations ditolak karena tugas ini tidak "
              "dijalankan dari konsol atau chat pribadi pemilik.")
    fails += check("access denied ID (refused run)", run(denied, True)[0], "failed")
    fails += check("cfo/hrd denied (refused run)", run(
        "Akses ke agen CFO dan HRD ditolak karena tugas ini tidak dijalankan dari akun pemilik.", True)[0], "failed")
    fails += check("unverifiable (refused run)", run(
        "Data operasional dan chat cabang tidak dapat diverifikasi.", True)[0], "failed")
    fails += check("denied inside marker (refused run)", run(
        "thinking\n" + M.format("Laporan gagal dibuat karena akses ke data pemilik ditolak dari chat ini."), True)[0], "failed")
    fails += check("by session id", (runs.start("scheduler_r2_t2", {"id": "t2"}), runs.note_refusal("scheduler_r2_t2"),
                                     out.classify(denied, "scheduler_r2_t2")[0])[2], "failed")
    runs.finish("scheduler_r2_t2")
    # The exact refusal text LRMagent returns fails even without the recorded refusal.
    fails += check("verbatim refusal", run("Gagal: This uses the owner's personal accounts, so only the owner can use "
                                           "it: from the LRMagent console or the WhatsApp number's own chat.", False)[0], "failed")
    # Short real reports with the same words are sent when the run was not refused.
    for legit_short in ("cuti Budi ditolak pemilik toko", "transfer tidak dapat diverifikasi bank",
                        "Refund ditolak owner cabang", "Akses CCTV sempat ditolak", denied):
        fails += check(f"not refused: {legit_short[:30]}", run(legit_short, False), ("final", legit_short))
    # A real report that merely mentions a rejected transaction still sends.
    legit = ("Laporan Operasional 15:00 WIB\n- Penjualan: 24 nota\n- 1 pengajuan tempo ditolak "
             "oleh approval (Toko Sinar)\n- Stok aman. Semua sudah diproses, tidak ada kendala.")
    fails += check("legit mentions ditolak", run(legit, True), ("final", legit))

    # Markers: the last non-empty block wins, any case; empty only when every block is empty.
    fails += check("echoed empty marker after report", out.classify(
        M.format("Laporan: 9 hadir.") + "\nSelesai, bukan " + M.format("") + " kosong."), ("final", "Laporan: 9 hadir."))
    fails += check("lower-case marker", out.classify("catatan\n[kirim]Stok aman.[/kirim]"), ("final", "Stok aman."))
    fails += check("all empty", out.classify(M.format("") + " lalu " + M.format(" ")), ("silent", ""))

    # A failed run is never delivered: integration stops on "failed" and only the owner is told.
    told = []
    agent = types.ModuleType("agent")
    agent.lrm_activity = types.SimpleNamespace(_agent_name=lambda a: "Lorium",
                                               _notify_owner=lambda a, text: told.append((a, text)))
    sys.modules["agent"], sys.modules["agent.lrm_activity"] = agent, agent.lrm_activity
    outcome, reason = out.classify("")
    out.note_failed("lorium", {"name": "Laporan harian"}, reason)
    fails += check("failed run -> owner only", (outcome, len(told), "Nothing was sent" in told[0][1]), ("failed", 1, True))
    del sys.modules["agent"], sys.modules["agent.lrm_activity"]

    print("FAILED" if fails else "ALL PASSED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
