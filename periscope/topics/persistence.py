"""Explicit topic-run ingestion and compatibility helpers."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys

from . import runs as runstore
from .render import emit
from periscope.runtime import paths


ROOT = paths.ROOT

def _display(path: Path) -> str:
    """Path relative to the repo when inside it, absolute otherwise."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _ingest(shard: Path) -> int:
    """Run the incremental ingest for one shard, in a child process.

    A child process because `tools/ingest_all.py` builds its own corpus handle,
    prints its own report, and must not share an event loop or a sqlite
    connection with the search path.
    """
    env = paths.subprocess_env()
    try:
        rel = str(Path(shard).relative_to(ROOT))
    except ValueError:
        rel = str(shard)
    proc = subprocess.run(
        [sys.executable, "-m", "periscope.operations.ingest", "--only", rel],
        capture_output=True, timeout=1800, cwd=str(ROOT), env=env)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
    for line in tail:
        emit("  " + line.strip())
    if proc.returncode != 0 and err.strip():
        emit("  " + err.strip().splitlines()[-1][:200])
    return proc.returncode


def ingest_run(path: Path, *, payload: dict | None = None) -> int:
    """Convert a replayable run to collector rows before invoking ingestion."""
    if payload is None:
        payload = runstore.read(path)
    row_file = _materialise(payload)
    try:
        return _ingest(row_file)
    finally:
        try:
            row_file.unlink(missing_ok=True)
        except OSError:
            pass


def _ingest_last() -> int:
    path = runstore.latest()
    if path is None:
        emit("没有可入库的分片：%s 为空" % _display(paths.TOPIC_DIR))
        return 2
    payload = runstore.read(path)
    emit("入库: %s（topic=%s, %d 行）"
         % (_display(path), payload.get("topic"), len(payload.get("rows") or [])))
    return ingest_run(path, payload=payload)


def _materialise(payload: dict) -> Path:
    """Write the run's rows as an HTTP-shaped ingest file, in a temp directory.

    The conversion and the write both live in `runstore`, because
    `tools/topic_persist.py` needs exactly the same thing and had its own,
    broken, version of it. This wrapper stays so the call site reads as one
    step, and so the temp directory is named here rather than inside the store.

    Two things the old inline version got wrong, both silent:

    1. It wrote beside the run shard, i.e. into `<DATA_DIR>/topics/`. That is
       the directory `runstore.latest()` sorts by *filename*, and
       `http_items.topic.json` sorts after every `2026...json`. One
       `--ingest-last` therefore made the next `--ingest-last` (and
       `dump_shard`) read the leftover instead of the newest run.
    2. The name was fixed, so two concurrent ingests collided.
    """
    return runstore.write_ingest_file(payload, directory=paths.SCRATCH)
