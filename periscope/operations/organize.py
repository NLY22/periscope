"""Relocate known root artifacts with a checked, collision-preserving manifest."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


LOG_NAMES = {".benchmark.txt", ".breadth.txt", ".collect_http.txt", ".eval_stress.txt",
             ".eval_deep.txt", ".eval_multi.txt", ".fanout_out.txt", ".fast_mode.txt",
             ".login_now.txt", ".login_progress.txt", ".probe_concurrent.txt",
             ".probe_source_cost.txt", ".topic_out.txt"}


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("artifact path is outside the workspace: " + str(path))
    return resolved


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plan_artifacts(workspace: Path, collect_dir: Path, report_dir: Path) -> list[dict]:
    root = Path(workspace).resolve()
    collect = _inside(Path(collect_dir), root)
    reports = _inside(Path(report_dir), root)
    plans, targets = [], set()
    for source in sorted(root.iterdir()):
        if not source.is_file():
            continue
        name = source.name
        if (name.startswith("http_items") and source.suffix in (".json", ".txt")) or (name.startswith("ugc_") and source.suffix == ".json"):
            directory = collect
        elif name in LOG_NAMES or name in ("endpoint_report.json", "search_sites.json", "dom_report.json") or (name.startswith("deep_search_") and source.suffix == ".json"):
            directory = reports
        else:
            continue
        _inside(source, root)
        digest = _digest(source)
        target = directory / name
        if target == source:
            continue
        suffix = 0
        while target.exists() or target in targets:
            suffix += 1
            target = directory / (source.stem + ".legacy-" + digest[:10] + ("-" + str(suffix) if suffix > 1 else "") + source.suffix)
        targets.add(target)
        plans.append({"source": str(source), "target": str(target), "sha256": digest,
                      "bytes": source.stat().st_size, "status": "planned"})
    return plans


def apply_artifacts(plans: list[dict], workspace: Path, manifest: Path) -> None:
    root = Path(workspace).resolve()
    manifest = _inside(Path(manifest), root)
    if manifest.exists():
        raise ValueError("migration manifest already exists")
    seen_targets = set()
    for plan in plans:
        source, target = _inside(Path(plan["source"]), root), _inside(Path(plan["target"]), root)
        if manifest in (source, target):
            raise ValueError("migration manifest cannot replace an artifact")
        if not source.is_file() or target.exists() or target in seen_targets:
            raise ValueError("artifact source/target changed since planning")
        if plan.get("sha256") != _digest(source):
            raise ValueError("artifact contents changed since planning")
        seen_targets.add(target)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"workspace": str(root), "created_at": datetime.now(timezone.utc).isoformat(), "files": plans}
    def record():
        temporary = manifest.with_suffix(".json.tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        temporary.replace(manifest)
    record()
    for plan in plans:
        source, target = Path(plan["source"]), Path(plan["target"])
        target.parent.mkdir(parents=True, exist_ok=True)
        # Path.rename does not replace existing files on Windows.
        if target.exists():
            raise ValueError("artifact destination appeared during relocation")
        source.rename(target)
        plan["status"] = "moved"
        record()


def main(argv=None) -> int:
    from periscope.runtime import paths
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the relocation; default only prints the plan")
    args = parser.parse_args(argv)
    if paths.WORKSPACE is None:
        parser.error("root cleanup requires a source checkout")
    plans = plan_artifacts(paths.WORKSPACE, paths.COLLECT_DIR, paths.REPORT_DIR)
    result = {"mode": "apply" if args.apply else "dry_run", "files": len(plans),
              "bytes": sum(plan["bytes"] for plan in plans), "plan": plans}
    if args.apply and plans:
        manifest = paths.REPORT_DIR / ("layout-migration-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        apply_artifacts(plans, paths.WORKSPACE, manifest)
        result["manifest"] = str(manifest)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
