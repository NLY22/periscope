"""Durable, replayable batches of collected evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from periscope.operations.ingest import build
from periscope.topics.runs import _row_for_disk, ingestable_rows


def _corpus_id(corpus) -> str:
    return hashlib.sha256(str(Path(corpus.path).resolve()).encode("utf-8")).hexdigest()


def replay_batch(corpus, path: Path) -> dict:
    """Validate a complete batch before inserting its records into a corpus."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        if not text.endswith("\n") or len(text.splitlines()) != 1:
            raise ValueError("incomplete evidence batch")
        payload = json.loads(text)
        if (not isinstance(payload, dict) or payload.get("version") != 1
                or not isinstance(payload.get("batch_id"), str) or not payload["batch_id"]
                or not isinstance(payload.get("created_at"), str) or not payload["created_at"]
                or "round_id" in payload and not isinstance(payload["round_id"], str)
                or not isinstance(payload.get("rows"), list)
                or any(not isinstance(row, dict) for row in payload["rows"])):
            raise ValueError("invalid evidence batch")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid evidence batch") from exc
    items, skipped = build(ingestable_rows(payload), [])
    count = corpus.add_items(items)
    from .versions import EvidenceVersions
    versions = EvidenceVersions(corpus).record_batch(payload)
    if payload.get("round_id"):
        from periscope.investigations.store import InvestigationStore
        store = InvestigationStore(corpus)
        record = store.get_round(payload["round_id"])
        if record and record["status"] in {"running", "interrupted", "failed"}:
            store.attach_batch(payload["round_id"], payload["batch_id"])
            for reference in versions:
                store.attach(payload["round_id"], **reference, recover=True, batch_id=payload["batch_id"])
    return {"new": count, "offered": len(items), "skipped": dict(skipped), "versions": versions}


def save_batch(corpus, rows: list[dict], *, round_id: str | None = None) -> dict:
    """Flush a complete batch beside the corpus before attempting insertion."""
    directory = Path(corpus.path).resolve().parent / "evidence-journal"
    directory.mkdir(parents=True, exist_ok=True)
    batch_id = uuid4().hex
    path = directory / (batch_id + ".jsonl")
    temp = directory / (batch_id + ".tmp")
    payload = {"version": 1, "batch_id": batch_id,
               "corpus_id": _corpus_id(corpus),
               "created_at": datetime.now(timezone.utc).isoformat(),
               "rows": [_row_for_disk(row, row.get("_origin") or "crawl") for row in rows]}
    if round_id is not None:
        payload["round_id"] = round_id
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return {**replay_batch(corpus, path), "batch_id": batch_id}


def replay_directory(corpus, directory: Path, *, owner_only: bool = False) -> dict:
    """Replay valid batches, reporting corrupted files without discarding them."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("journal directory does not exist")
    result = {"new": 0, "batches": 0, "failed": [], "skipped": {}}
    skipped = Counter()
    batches = []
    for path in directory.glob("*.jsonl"):
        try:
            header = json.loads(path.read_text(encoding="utf-8"))
            if owner_only and isinstance(header, dict) and header.get("corpus_id") != _corpus_id(corpus):
                continue
            if not isinstance(header, dict) or not isinstance(header.get("created_at"), str):
                raise ValueError("invalid evidence batch")
            batches.append((header["created_at"], path.name, path))
        except (ValueError, OSError, UnicodeError) as exc:
            result["failed"].append({"batch": path.name, "error": type(exc).__name__})
            continue
    for _, _, path in sorted(batches):
        try:
            outcome = replay_batch(corpus, path)
        except (ValueError, OSError, UnicodeError) as exc:
            result["failed"].append({"batch": path.name, "error": type(exc).__name__})
            continue
        result["new"] += outcome["new"]
        result["batches"] += 1
        skipped.update(outcome["skipped"])
    result["skipped"] = dict(skipped)
    return result


def main() -> int:
    import argparse
    from src.corpus.store import Corpus

    parser = argparse.ArgumentParser(description="Replay evidence batches into a corpus")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    if not args.journal.is_dir():
        parser.error("journal directory does not exist")
    corpus = Corpus(args.corpus)
    try:
        result = replay_directory(corpus, args.journal)
    finally:
        corpus.close()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["failed"] or result["skipped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
