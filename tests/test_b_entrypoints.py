"""B's fixed entry points still start.

Each script is launched the way a user launches it (as a file, from the repo
root), with `--help` so nothing is fetched or written. This catches the class
of failure where a moved script leaves a stale path or an undefined `ROOT`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _help(relative: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, relative, "--help"],
        cwd=str(ROOT), env=env, capture_output=True,
        encoding="utf-8", errors="replace", timeout=90,
    )


def test_topic_entrypoint_starts():
    proc = _help("topic.py")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "任意话题检索" in proc.stdout


def test_topic_persist_entrypoint_starts():
    proc = _help("tools/topic_persist.py")
    assert proc.returncode == 0, proc.stderr[-800:]


def test_backfill_entrypoint_starts():
    proc = _help("collectors/backfill_fulltext.py")
    assert proc.returncode == 0, proc.stderr[-800:]
