"""Replayable topic runs: raw layered rows, query inputs and collection statistics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import hashlib
import json
import os
import re
import tempfile

from periscope.runtime import paths
from periscope.runtime.files import replace_file


VERSION = 1


def _slug(text: str, limit: int = 40) -> str:
    keep = []
    for ch in (text or "").strip():
        if ch.isalnum() or ch in "-_":
            keep.append(ch)
        elif ch.isspace() or ch in "，。、；：！？/\\|<>:\"*?":
            keep.append("_")
        if len(keep) >= limit:
            break
    out = "".join(keep).strip("_")
    return out or "topic"


def path_for(topic: str, created_at: Optional[datetime] = None) -> Path:
    """Where this run's shard goes: `<DATA_DIR>/topics/<stamp>_<topic>.json`."""
    stamp = (created_at or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return paths.TOPIC_DIR / ("%s_%s.json" % (stamp, _slug(topic)))


def _row_for_disk(row: Dict[str, Any], layer: str) -> Dict[str, Any]:
    """Copy the fields worth keeping; drop process-local helpers and secrets.

    `_layer` is authoritative and is set here rather than trusted from the row:
    the same dict is reused by the merge, and a stale tag would silently make a
    local row look like a fresh API result.
    """
    keep = (
        "title", "url", "author", "publisher", "date", "published_at",
        "body", "text", "snippet", "trust", "source", "source_type",
        "authority", "rerank", "_origin",
        "kind", "provenance", "body_source_url", "sources", "published_text", "document",
        # fields a collector adds once enrichment exists
        "cid", "bvid", "aid", "metrics", "transcript", "comments", "replies", "answers", "ids",
    )
    out = {k: row[k] for k in keep if k in row}
    # An empty metrics dict is not data. Keeping it would make every platform
    # row look like it carried engagement, and the coverage table -- the whole
    # point of this file -- would report 344/344 instead of the 47 that were
    # actually measured.
    for name in ("metrics", "ids", "transcript", "comments", "replies", "answers"):
        if not out.get(name):
            out.pop(name, None)
    out["_layer"] = layer
    return out


def write(
    topic: str,
    rows: Iterable[Dict[str, Any]],
    *,
    argv: Optional[List[str]] = None,
    elapsed: float = 0.0,
    stats: Optional[Dict[str, Any]] = None,
    notes: Optional[List[str]] = None,
    created_at: Optional[datetime] = None,
) -> Path:
    """Persist one run. Returns the file it wrote."""
    created = created_at or datetime.now()
    path = path_for(topic, created)
    payload = {
        "version": VERSION,
        "topic": topic,
        "created_at": created.isoformat(),
        "argv": list(argv or []),
        "elapsed": round(float(elapsed), 2),
        "stats": dict(stats or {}),
        "notes": list(notes or []),
        "rows": [_row_for_disk(r, r.get("_origin") or "unknown") for r in rows],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    replace_file(tmp, path)    # atomic on Windows and POSIX alike
    return path


def read(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def ingestable_rows(payload: Optional[Dict[str, Any]]) -> "list[Dict[str, Any]]":
    """The rows of a run shard that `ingest_all` should be given, as HTTP rows.

    A run shard is `{"version": ..., "topic": ..., "rows": [...]}`, not a list.
    Two callers needed the same conversion and each got it slightly wrong:

    - `topic.py --ingest-last` converted correctly but wrote the result into the
      topic directory (fixed separately).
    - `tools/topic_persist.py` iterated the *payload* as if it were the row
      list, so `r.get("url")` ran on the string `"version"` and the whole pass
      died with `AttributeError: 'str' object has no attribute 'get'`.

    Keeping the conversion here is what makes "read a shard" and "hand its rows
    to the ingest" one operation instead of an agreement between modules.

    Local rows are dropped: they came out of the corpus, so re-offering them is
    a no-op that inflates the "offered" count and hides real rows.
    """
    rows: "list[Dict[str, Any]]" = []
    for row in (payload or {}).get("rows") or []:
        if not isinstance(row, dict):
            continue
        row = dict(row)
        layer = row.pop("_layer", "")
        if layer == "local":
            continue
        if layer == "api":
            row.setdefault("source", "dsapi")
            row.setdefault("kind", "media")
        elif layer == "platform":
            row.setdefault("kind", "community")
        if not row.get("source"):
            continue
        rows.append(row)
    return rows


def write_ingest_file(payload: Optional[Dict[str, Any]],
                      directory: Optional[Path] = None) -> Path:
    """Materialise a run shard as a collector-shaped `http_items.*.json` file.

    `ingest_all --only` reads a *list* of rows. A run shard is a dict with a
    `rows` key, so handing one straight to the ingest fails with
    `AttributeError: 'str' object has no attribute 'get'` - it iterates the
    dict, gets the keys, and asks the string `"version"` for a `.get`.

    That is what `tools/topic_persist.py` did: it passed the run shard path to
    `ingest_all --only` and the ingest crashed. `topic.py --ingest-last` had
    already grown its own conversion, which is how the two callers came to
    disagree. One function, both callers.

    The file is written under `directory` (default: the scratch tree) with the
    `http_items.` prefix the incremental ingest keys on, and a random suffix so
    concurrent ingests cannot collide. Callers are expected to delete it.
    """
    rows = ingestable_rows(payload)
    target_dir = Path(directory or paths.SCRATCH)
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="http_items.topic.", suffix=".json", dir=str(target_dir))
    out = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return out


#: A run file is named `<YYYYmmdd-HHMMSS>_<slug>.json`. Anything else in the
#: directory is not a run: a stray `http_items.topic.json` once sorted after
#: every real run and made `latest()` return it forever.
_NAME_RE = re.compile(r"^\d{8}-\d{6}_")


def _run_files(base: Path) -> "list[Path]":
    """Run shards in `base`, ignoring files that are not runs.

    Filtering on the name rather than on `read()` keeps the check cheap and
    keeps a corrupt run visible: a file that *is* a run but will not parse
    should raise when someone opens it, not silently disappear from the list.
    """
    if not base.exists():
        return []
    return [p for p in base.glob("*.json") if _NAME_RE.match(p.name)]


def latest(topic_dir: Optional[Path] = None) -> Optional[Path]:
    """The newest run file.

    Ordered by *name*, not mtime: every name starts with a `YYYYmmdd-HHMMSS`
    stamp, so the name is the run's creation time. mtime is the wrong key here
    for two reasons that both show up in practice - a copy or a restore rewrites
    it, and two runs inside the same second can share it, which makes `max()`
    return whichever the directory listing happened to yield first.
    """
    files = _run_files(Path(topic_dir or paths.TOPIC_DIR))
    return max(files, key=lambda p: p.name) if files else None


def find(topic: str, topic_dir: Optional[Path] = None) -> Optional[Path]:
    """The newest run file for one topic (slug match), same ordering rule."""
    slug = _slug(topic)
    files = [p for p in _run_files(Path(topic_dir or paths.TOPIC_DIR))
             if p.stem.endswith("_" + slug)]
    return max(files, key=lambda p: p.name) if files else None


def digest(topic: str, argv: Optional[List[str]] = None) -> str:
    """Stable id for a run's inputs — for dedup and for test assertions."""
    raw = "|".join([topic, *sorted(argv or [])])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
