"""Explicit run ingestion followed by optional full-text recovery and cache merge."""

from __future__ import annotations

from pathlib import Path
import argparse
import os
import re
import subprocess
import sys

from periscope.runtime import paths
from periscope.topics import runs as runstore


ROOT = paths.ROOT

INGEST = "periscope.operations.ingest"
BACKFILL = "periscope.operations.backfill"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", required=True)
    ap.add_argument("--fetch-body", type=int, default=40)
    args = ap.parse_args()

    env = paths.subprocess_env()
    log = paths.REPORT_DIR / "topic_persist.log"
    lines = []

    # `--shard` may be absolute, relative to the shard directory, or relative
    # to the repo root. Resolve all three instead of assuming one: the caller
    # (topic.py / collector) writes into paths.SHARD_DIR, not the repo root.
    raw = Path(args.shard)
    if raw.is_absolute():
        shard_path = raw
    elif (paths.COLLECT_DIR / raw).exists():
        shard_path = paths.COLLECT_DIR / raw
    elif (paths.SHARD_DIR / raw).exists():
        shard_path = paths.SHARD_DIR / raw
    else:
        shard_path = ROOT / raw
    if not shard_path.exists():
        lines.append("分片不存在: %s" % args.shard)
        log.write_text("\n".join(lines), encoding="utf-8")
        return 1

    # A run shard is `{"topic": ..., "rows": [...]}`, not a list of rows, and
    # `ingest_all --only` reads a list. This script used to hand the shard
    # straight to the ingest, which then iterated the dict's keys and asked the
    # string `"version"` for `.get`:
    #
    #     AttributeError: 'str' object has no attribute 'get'
    #
    # The ingest put that on stderr after doing nothing, so the run reported
    # `ingest rc=1` and the body-fetch half never had rows either.
    try:
        payload = runstore.read(shard_path)
    except Exception as exc:  # noqa: BLE001
        lines.append("分片读取失败: %s: %s" % (type(exc).__name__, exc))
        log.write_text("\n".join(lines), encoding="utf-8")
        return 2

    ingest_file = runstore.write_ingest_file(payload, directory=paths.SCRATCH)

    # ---------- 入库 ----------
    # `ingest_all --only` resolves relative to ROOT. Pass a relative path when
    # the file is inside the repo, so Windows pathlib's "absolute right side"
    # behaviour never decides the target.
    try:
        only_arg = str(ingest_file.relative_to(ROOT))
    except ValueError:
        only_arg = str(ingest_file)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", INGEST,
             "--only", only_arg],
            capture_output=True, timeout=1800, cwd=str(ROOT), env=env)
        out = (proc.stdout or b"").decode("utf-8", "replace")
        m = re.search(r"items after\s*:\s*(\d+)", out)
        if proc.returncode == 0:
            lines.append("ingest ok: %s items" % (m.group(1) if m else "?"))
        else:
            err = (proc.stderr or b"").decode("utf-8", "replace")
            lines.append("ingest rc=%d: %s"
                         % (proc.returncode,
                            err.strip().splitlines()[-1][:120] if err.strip() else ""))
    except Exception as exc:  # noqa: BLE001
        lines.append("ingest %s: %s" % (type(exc).__name__, exc))
    finally:
        # The materialised file is a build product for one ingest call. Leaving
        # it behind is how the topic directory came to hold a file that sorted
        # after every real run.
        try:
            ingest_file.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------- 抓正文 ----------
    # The payload was already read above; re-reading it here would be the same
    # thing twice, and the earlier version's bug was precisely reading the shard
    # in two different shapes.
    if args.fetch_body > 0:
        rows = runstore.ingestable_rows(runstore.read(shard_path))

        urls, seen = [], set()
        for r in rows:
            u = (r.get("url") or "").strip()
            if not u.startswith("http") or u in seen:
                continue
            seen.add(u)
            if any(x in u for x in (".pdf", "/link?", "sogou.com")):
                continue
            urls.append(u)
            if len(urls) >= args.fetch_body:
                break

        if urls:
            # The URL list goes to scratch, not next to the shard: a `.urls.txt`
            # beside a run also survives as an unexplained artifact in the topic
            # directory.
            uf = paths.SCRATCH / (shard_path.stem + ".urls.txt")
            uf.write_text("\n".join(urls), encoding="utf-8")
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", BACKFILL,
                     "--urls-file", str(uf), "--concurrency", "12"],
                    capture_output=True, timeout=600, cwd=str(ROOT), env=env)
                out = (proc.stdout or b"").decode("utf-8", "replace")
                m = re.search(r"recovered (\d+) items", out)
                lines.append("bodies: %s / %d"
                             % (m.group(1) if m else "0", len(urls)))
            except Exception as exc:  # noqa: BLE001
                lines.append("backfill %s" % type(exc).__name__)
            finally:
                try:
                    uf.unlink(missing_ok=True)
                except OSError:
                    pass

            # 把正文缓存合并回语料
            try:
                subprocess.run(
                    [sys.executable, "-m", INGEST,
                     "--fulltext-only"],
                    capture_output=True, timeout=600, cwd=str(ROOT), env=env)
                lines.append("fulltext merged")
            except Exception as exc:  # noqa: BLE001
                lines.append("merge %s" % type(exc).__name__)

    log.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
