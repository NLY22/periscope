"""Browser adapters share the direct-retrieval deadline and source filters."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from periscope.runtime import paths
from ..platforms.browser import PROFILE_BUSY_EXIT
from .worker import run_worker


BROWSER_KEYS = {"xhs": "xhs_explore", "douyin": "douyin_hot", "kuaishou": "kuaishou_hot"}


def collect(topic: str, scripts: dict, modules: dict, *, started: float,
            budget: float, plan=None) -> tuple[list[dict], list[dict]]:
    candidates = list(scripts)
    entries = {}
    if plan is not None:
        entries = {entry.key: entry for entry in plan.skipped
                   if entry.mode == "browser" and plan.reasons.get(entry.key) != "disabled"}
        candidates = [key for key in candidates if BROWSER_KEYS[key] in entries]
    rows, reports = [], []
    for index, key in enumerate(candidates):
        source_key = BROWSER_KEYS[key] if plan is not None else key
        entry = entries.get(source_key)
        report = {**(entry.describe() if entry else {"key": source_key, "mode": "browser"}),
                  "status": "over_budget", "attempted": False, "rows": 0, "elapsed": 0.0, "error": ""}
        reports.append(report)
        remaining = max(0, budget - (time.monotonic() - started)) if budget else 120
        if remaining <= 0:
            continue
        module = modules[key]
        if importlib.util.find_spec(module) is None:
            report.update(status="failed", error="missing_adapter")
            continue
        # Each platform gets a share of the remaining deadline.
        timeout = remaining / (len(candidates) - index) if budget else remaining
        tick = time.monotonic()
        report["attempted"] = True
        try:
            paths.SHARD_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="browser-" + key + "-", dir=paths.SHARD_DIR) as directory:
                out = Path(directory) / Path(scripts[key][1]).name
                try:
                    proc = run_worker([sys.executable, "-m", module, "--queries", topic,
                                           "--scrolls", "5", "--out", str(out)],
                                          capture_output=True, timeout=timeout,
                                          cwd=str(paths.ROOT), env=paths.subprocess_env())
                    if proc.returncode:
                        if proc.returncode == PROFILE_BUSY_EXIT:
                            error = "profile_busy"
                        elif key == "kuaishou" and proc.returncode == 4:
                            error = "captcha_required"
                        else:
                            error = "exit_%d" % proc.returncode
                        report.update(status="failed", error=error)
                except subprocess.TimeoutExpired as exc:
                    report.update(status="over_budget", error="browser_deadline")
                    if getattr(exc, "cleanup_failed", False):
                        report["cleanup_error"] = "process_tree_cleanup_failed"
                if not out.exists():
                    if not report["error"]:
                        report.update(status="failed", error="no_output")
                    continue
                try:
                    data = json.loads(out.read_text(encoding="utf-8-sig"))
                    if not isinstance(data, list):
                        raise ValueError("browser output must be a list")
                except (OSError, ValueError):
                    report["checkpoint_error"] = "invalid_output"
                    if not report["error"]:
                        report.update(status="failed", error="invalid_output")
                    continue
                for raw in data:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        url = urlsplit(str(raw.get("url") or ""))
                        if url.scheme not in ("http", "https") or not url.hostname:
                            continue
                    except ValueError:
                        continue
                    row = dict(raw, source=source_key, kind=entry.kind if entry else "ugc")
                    row["provenance"] = {"source_key": source_key, "method": "browser",
                                         "identity": "url",
                                         "observed_at": datetime.now(timezone.utc).isoformat(),
                                         "url": row["url"], "content_role": "community"}
                    rows.append(row)
                    report["rows"] += 1
                if report["error"]:
                    if report["rows"]:
                        report["status"] = "partial"
                else:
                    report["status"] = "ok" if report["rows"] else "empty"
        except Exception as exc:
            report.update(status="failed", error=type(exc).__name__)
        finally:
            report["elapsed"] = round(time.monotonic() - tick, 3)
    return rows, reports
