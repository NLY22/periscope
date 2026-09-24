"""Evidence corpus: a persistent, searchable store of every fetched item.

Horizon's original design was stateless-by-default: each run fetched,
analysed, published a daily briefing, and the raw material drifted away.
Periscope's research loop needs the opposite — everything ever collected
stays queryable, so the agent can answer not just "what does the web say
today" but "when did this claim first appear, who repeated it, and did
anyone contradict it".

Schema
------
items            one row per fetched ContentItem (append-only, deduped
                 by natural id), carrying a SimHash fingerprint and a
                 duplicate-cluster id.
items_fts        FTS5 shadow table over title+content+author for native
                 SQLite full-text search (trigram tokenizer keeps CJK
                 usable without a segmentation library).
runs             one row per pipeline run: counts and duration, so the
                 corpus itself is auditable (what did we know on day X).

Clusters are recomputed per-run over recent history; the stored value is
best-effort grouping used by stats/UI, never a hard dedup (nothing is
deleted — independent-source counting needs the duplicates to remain).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..models import ContentItem
from .simhash import cluster_pairs, fingerprint

SCHEMA_VERSION = 1

_MASK64 = (1 << 64) - 1


def _as_signed64(value: int) -> int:
    """SQLite INTEGER is signed 64-bit; map unsigned fingerprints over."""
    value &= _MASK64
    return value - (1 << 64) if value >= (1 << 63) else value

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    fingerprint INTEGER NOT NULL,
    cluster_id TEXT,
    run_id INTEGER REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_type);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_id);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, content, author,
    content='items', content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, content, author)
    VALUES (new.rowid, new.title, new.content, new.author);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, content, author)
    VALUES ('delete', old.rowid, old.title, old.content, old.author);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE OF title, content, author ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, content, author)
    VALUES ('delete', old.rowid, old.title, old.content, old.author);
    INSERT INTO items_fts(rowid, title, content, author)
    VALUES (new.rowid, new.title, new.content, new.author);
END;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    since TEXT NOT NULL,
    items_new INTEGER NOT NULL DEFAULT 0,
    items_total_seen INTEGER NOT NULL DEFAULT 0,
    note TEXT
);
"""


class Corpus:
    """SQLite-backed evidence store."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ run
    def begin_run(self, since: datetime) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (started_at, since) VALUES (?, ?)",
            (_utc_now().isoformat(), since.astimezone(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        items_new: int,
        items_total_seen: int,
        note: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at=?, items_new=?, items_total_seen=?, note=? WHERE id=?",
            (_utc_now().isoformat(), items_new, items_total_seen, note, run_id),
        )
        self._conn.commit()

    # ---------------------------------------------------------------- write
    def add_items(self, items: Iterable[ContentItem], run_id: Optional[int] = None) -> int:
        """Insert content items; existing ids are skipped (append-only).

        Returns the number of newly stored rows.
        """
        rows = []
        for item in items:
            content = item.content or ""
            text_for_fp = f"{item.title}\n{content}"
            rows.append(
                (
                    item.id,
                    item.source_type.value,
                    item.title,
                    str(item.url),
                    item.author,
                    item.published_at.astimezone(timezone.utc).isoformat(),
                    item.fetched_at.astimezone(timezone.utc).isoformat(),
                    content,
                    json.dumps(_jsonable(item.metadata), ensure_ascii=False),
                    _as_signed64(fingerprint(text_for_fp)),
                    run_id,
                )
            )
        before = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self._conn.executemany(
            """INSERT OR IGNORE INTO items
               (id, source_type, title, url, author, published_at, fetched_at,
                content, metadata_json, fingerprint, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self._conn.commit()
        after = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        return int(after - before)

    # ---------------------------------------------------------------- read
    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """FTS5 BM25-ranked full-text search over stored evidence.

        User text is sanitised into a conjunction of quoted phrases so FTS
        operators (NOT, OR, column filters, hyphens-as-`col`) can never
        leak into query syntax. Terms shorter than 3 chars can't be matched
        by the trigram tokenizer (common for CJK words like 工作/注册), so
        they fall back to an escaped LIKE scan.
        """
        fts_terms, like_terms = [], []
        for term in query.split():
            term = term.strip('"')
            if not term:
                continue
            (like_terms if len(term) < 3 else fts_terms).append(term)

        results: Dict[str, Dict[str, Any]] = {}
        if fts_terms:
            match = " AND ".join('"' + t.replace('"', '""') + '"' for t in fts_terms)
            rows = self._conn.execute(
                """SELECT i.*, bm25(items_fts) AS rank
                   FROM items_fts
                   JOIN items i ON i.rowid = items_fts.rowid
                   WHERE items_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (match, limit),
            ).fetchall()
            for r in rows:
                d = self._row_to_dict(r)
                results[d["id"]] = d
        for term in like_terms:
            if len(results) >= limit:
                break
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            rows = self._conn.execute(
                """SELECT * FROM items
                   WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'
                   ORDER BY published_at DESC LIMIT ?""",
                (like, like, limit - len(results)),
            ).fetchall()
            for r in rows:
                d = self._row_to_dict(r)
                results.setdefault(d["id"], d)
        return list(results.values())[:limit]

    def document_frequency(self, term: str) -> int:
        """How many stored items contain `term` at all (exact substring).

        Term-scoring for query planning, not user-facing search: substring
        LIKE is deliberately stricter here than the trigram tokenizer, which
        would match "开源大" inside an unrelated "...开源大比拼...". Corpus is
        small by design (one user's collection), so the scan is fine.
        """
        term = term.strip()
        if not term:
            return 0
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        return self._conn.execute(
            "SELECT COUNT(*) FROM items WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'",
            (like, like),
        ).fetchone()[0]

    def recent(
        self,
        limit: int = 50,
        source_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM items"
        params: list[Any] = []
        if source_type:
            sql += " WHERE source_type=?"
            params.append(source_type)
        sql += " ORDER BY published_at DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_dict(r) for r in self._conn.execute(sql, params)]

    def stats(self) -> Dict[str, Any]:
        total = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        by_source = dict(
            self._conn.execute(
                "SELECT source_type, COUNT(*) FROM items GROUP BY source_type"
            ).fetchall()
        )
        clusters = self._conn.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM items WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
        runs = self._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        return {
            "items": total,
            "by_source": by_source,
            "clusters": clusters,
            "runs": runs,
            "path": str(self.path),
        }

    # ------------------------------------------------------------- clusters
    def recompute_clusters(
        self, max_distance: int = 3, lookback_rows: int = 500
    ) -> int:
        """Group near-duplicate recent items into clusters.

        Uses connected components over SimHash pairs within `max_distance`
        Hamming bits. Only the newest `lookback_rows` rows are re-grouped
        to bound per-run cost; older rows keep their historic cluster id.

        Returns the number of rows whose cluster_id changed.
        """
        rows = self._conn.execute(
            "SELECT rowid, fingerprint FROM items ORDER BY rowid DESC LIMIT ?",
            (lookback_rows,),
        ).fetchall()
        if not rows:
            return 0
        pairs = cluster_pairs(
            [(r["rowid"], r["fingerprint"]) for r in rows], max_distance
        )

        parent: Dict[int, int] = {r["rowid"]: r["rowid"] for r in rows}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in pairs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        updated = 0
        for rowid in parent:
            cluster_id = f"c{find(rowid)}"
            changed = self._conn.execute(
                "UPDATE items SET cluster_id=? WHERE rowid=? AND (cluster_id IS NULL OR cluster_id!=?)",
                (cluster_id, rowid, cluster_id),
            )
            updated += changed.rowcount
        self._conn.commit()
        return updated

    # ---------------------------------------------------------------- misc
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d.pop("rowid", None)
        if "metadata_json" in d:
            try:
                d["metadata"] = json.loads(d.pop("metadata_json"))
            except (json.JSONDecodeError, TypeError):
                d["metadata"] = {}
        return d

    def close(self) -> None:
        self._conn.close()


def _jsonable(obj: Any) -> Any:
    """Best-effort conversion of metadata to JSON-safe values."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
