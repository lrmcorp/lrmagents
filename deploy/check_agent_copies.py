"""Fail the build if an agent.lrm_* module is referenced in code but not COPYed into the image.

A lazy runtime import (e.g. `from agent import lrm_task_output` inside a function) is not
evaluated by `python -m compileall`, so a module left out of the Dockerfile's explicit COPY
list only fails when the code path runs — for lrm_task_output that was every scheduled report.
This check compares the lrm_* modules referenced across apply.py and the agent-tools against
the ones the Dockerfile copies to /app/agent/, and exits non-zero on any gap.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
copied = set(re.findall(r"/app/agent/(lrm_\w+)\.py", (ROOT / "deploy/Dockerfile").read_text()))
refs = set()
for f in [ROOT / "deploy/brand/apply.py", *(ROOT / "deploy/agent-tools").rglob("*.py")]:
    text = f.read_text(encoding="utf-8")
    refs |= set(re.findall(r"from agent import (lrm_\w+)", text))
    refs |= set(re.findall(r"from agent\.(lrm_\w+) import", text))
    refs |= set(re.findall(r"\bimport agent\.(lrm_\w+)", text))

missing = sorted(refs - copied)
if missing:
    print("::error::agent.lrm_* modules referenced but not COPYed into /app/agent/ in deploy/Dockerfile:", ", ".join(missing))
    sys.exit(1)
print(f"agent module COPY check OK: {len(refs)} referenced modules all present")
