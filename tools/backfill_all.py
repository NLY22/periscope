"""Drive a full-corpus body backfill in bounded batches, logging progress.

Why a driver instead of one `--limit 87000` call: the backfill selects its
candidates at startup and holds them in memory, so one huge run has no progress
checkpoint outside its own 20-row commits. Driving it in batches means each batch
re-selects from the database, so an interrupted run resumes at the unfetched
rows instead of replaying the selection it already did.

The loop stops when a batch recovers nothing twice in a row - that is the
signal that every remaining candidate is blocked (403/JS-rendered/timeout), not
that the next batch would do better.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.console import force_utf8_output  # noqa: E402

force_utf8_output()

SCRIPT = ROOT / "collectors" / "backfill_fulltext.py"
LOG = ROOT / ".periscope-scratch" / "backfill_run.log"


def _run_batch(size: int, concurrency: int) -> tuple[int, int, float]:
    """Returns (recovered, failed, elapsed).

    Parses the two summary lines the backfill prints:
        recovered 148 items, 1383608 chars total (avg 9348)
        too short 41, failed 11
    """
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--limit", str(size),
         "--concurrency", str(concurrency)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=3600,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    elapsed = time.monotonic() - t0
    out = proc.stdout or ""
    recovered = failed = 0
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("recovered "):
            parts = s.split()
            if len(parts) > 1 and parts[1].isdigit():
                recovered = int(parts[1])
        elif s.startswith("too short"):
            # The `failed` count sits on the same line as the short count.
            tokens = s.replace(",", " ").split()
            for idx, tok in enumerate(tokens):
                if tok == "failed" and idx + 1 < len(tokens):
                    nxt = tokens[idx + 1]
                    if nxt.isdigit():
                        failed = int(nxt)
                    break
    return recovered, failed, elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description="全库正文回填（分批，可中断续跑）")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--max-batches", type=int, default=200)
    ap.add_argument("--idle-stop", type=int, default=2,
                    help="连续多少批零产出就停（默认 2）")
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LOG.open("a", encoding="utf-8")

    total_ok = total_fail = 0
    idle = 0
    started = time.monotonic()
    for i in range(1, args.max_batches + 1):
        ok, fail, el = _run_batch(args.batch, args.concurrency)
        total_ok += ok
        total_fail += fail
        line = ("[%s] batch %3d  recovered=%-5d failed=%-4d elapsed=%5.1fs  "
                "total_ok=%d" % (time.strftime("%H:%M:%S"), i, ok, fail, el,
                                 total_ok))
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()
        if ok == 0:
            idle += 1
            if idle >= args.idle_stop:
                print("连续 %d 批零产出，停止" % idle, flush=True)
                break
        else:
            idle = 0

    total_el = time.monotonic() - started
    summary = ("done: %d batches, recovered %d, failed %d, %.1f min"
               % (i, total_ok, total_fail, total_el / 60))
    print(summary, flush=True)
    log.write(summary + "\n")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
