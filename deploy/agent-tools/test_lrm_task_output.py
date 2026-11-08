"""Standalone tests for the scheduled-run output classifier (python3 test_lrm_task_output.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrm_task_output as out


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

    print("FAILED" if fails else "ALL PASSED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
