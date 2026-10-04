"""Evidence corpus: a persistent, searchable store of every fetched item.

Periscope's original design was stateless-by-default: each run fetched,
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
from .sections import claimable_of, claimable_text, marker_sections_to_model
from .simhash import cluster_pairs, fingerprint
from .trust import compute_features, publisher_of, trust_score

SCHEMA_VERSION = 4

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
    claimable TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '',
    time_basis TEXT NOT NULL DEFAULT 'published',
    sections_json TEXT NOT NULL DEFAULT '[]',
    publisher TEXT,
    trust REAL,
    trust_features_json TEXT NOT NULL DEFAULT '{}',
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

-- Evidence-side index over the author-written layer only. Comment/reply text
-- stays searchable in items_fts (the panel wants recall), but claim linking
-- and grading must never rest on it, so they query this table instead.
CREATE VIRTUAL TABLE IF NOT EXISTS claim_fts USING fts5(
    title, claimable,
    content='items', content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS claim_ai AFTER INSERT ON items BEGIN
    INSERT INTO claim_fts(rowid, title, claimable)
    VALUES (new.rowid, new.title, new.claimable);
END;
CREATE TRIGGER IF NOT EXISTS claim_ad AFTER DELETE ON items BEGIN
    INSERT INTO claim_fts(claim_fts, rowid, title, claimable)
    VALUES ('delete', old.rowid, old.title, old.claimable);
END;
CREATE TRIGGER IF NOT EXISTS claim_au AFTER UPDATE OF title, claimable ON items BEGIN
    INSERT INTO claim_fts(claim_fts, rowid, title, claimable)
    VALUES ('delete', old.rowid, old.title, old.claimable);
    INSERT INTO claim_fts(rowid, title, claimable)
    VALUES (new.rowid, new.title, new.claimable);
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
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        # check_same_thread=False: this store is opened once and shared by
        # the CLI loop, the MCP server and the web panel, where "the thread
        # that opened it" is an implementation detail of the event loop
        # (uvicorn workers, TestClient portals). All callers are cooperative
        # async code on a single loop, so there is never real concurrency on
        # this connection; WAL keeps crash-consistency regardless.
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # Order matters: a pre-tiering database must grow the `claimable`
        # column *before* _SCHEMA creates claim_fts over it, otherwise the
        # index is built against a missing column and the file corrupts.
        legacy = self._backfill_legacy_layers()
        legacy = self._backfill_v3_identity() or legacy
        legacy = self._backfill_v4_trust() or legacy
        self._conn.executescript(_SCHEMA)
        if legacy:
            # External-content FTS tables are never auto-populated for rows
            # written before the index existed, so rebuild both mirrors.
            self._conn.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
            self._conn.execute("INSERT INTO claim_fts(claim_fts) VALUES('rebuild')")
        self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._conn.commit()

    def _backfill_legacy_layers(self) -> bool:
        """Add and fill the author-written column on a pre-tiering database.

        Claims linked before tiering already exist; without this backfill their
        evidence stops matching the moment linking becomes claimable-only,
        silently turning graded claims into `unsupported`.
        """
        if not self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchone():
            return False
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(items)")}
        if "claimable" in columns:
            return False
        self._conn.execute(
            "ALTER TABLE items ADD COLUMN claimable TEXT NOT NULL DEFAULT ''"
        )
        rows = self._conn.execute("SELECT rowid, content FROM items").fetchall()
        self._conn.executemany(
            "UPDATE items SET claimable=? WHERE rowid=?",
            [(claimable_text(r["content"]), r["rowid"]) for r in rows],
        )
        self._conn.commit()
        return True

    def _backfill_v3_identity(self) -> bool:
        """Add locator/time_basis/sections_json to a pre-v3 database.

        Rows written before structured sections existed get their sections
        reconstructed by the legacy marker scan and stamped `legacy_marker`,
        so a reader can tell an inferred tier from a declared one.
        """
        if not self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchone():
            return False
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(items)")}
        if "locator" in columns:
            return False
        self._conn.execute("ALTER TABLE items ADD COLUMN locator TEXT NOT NULL DEFAULT ''")
        self._conn.execute(
            "ALTER TABLE items ADD COLUMN time_basis TEXT NOT NULL DEFAULT 'published'"
        )
        self._conn.execute(
            "ALTER TABLE items ADD COLUMN sections_json TEXT NOT NULL DEFAULT '[]'"
        )
        rows = self._conn.execute("SELECT rowid, url, content FROM items").fetchall()
        self._conn.executemany(
            "UPDATE items SET locator=?, sections_json=? WHERE rowid=?",
            [
                (
                    r["url"],
                    json.dumps(
                        [s.model_dump() for s in marker_sections_to_model(r["content"])],
                        ensure_ascii=False,
                    ),
                    r["rowid"],
                )
                for r in rows
            ],
        )
        self._conn.commit()
        return True

    def _backfill_v4_trust(self) -> bool:
        """Add publisher/trust/trust_features_json and score the existing rows.

        Rows written before P1 have no declared authorship tier either, so
        their features are computed from the marker-inferred claimable layer.
        That is recorded in `provenance` inside the feature blob rather than
        hidden: a pre-P1 score is a weaker signal, and the reader can see it.
        """
        if not self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchone():
            return False
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(items)")}
        if "trust" in columns:
            return False
        self._conn.execute("ALTER TABLE items ADD COLUMN publisher TEXT")
        self._conn.execute("ALTER TABLE items ADD COLUMN trust REAL")
        self._conn.execute(
            "ALTER TABLE items ADD COLUMN trust_features_json TEXT NOT NULL DEFAULT '{}'"
        )
        rows = self._conn.execute(
            "SELECT rowid, source_type, author, claimable, url, locator, published_at,"
            " time_basis, sections_json FROM items"
        ).fetchall()
        updates = []
        for r in rows:
            publisher = publisher_of(r["author"], r["locator"] or "", r["url"] or "")
            features = compute_features(
                source_type=r["source_type"],
                text=r["claimable"] or "",
                author=r["author"],
                provenance="legacy_marker",
                published_at=_parse_time(r["published_at"]),
                time_basis=r["time_basis"] or "published",
            )
            updates.append((
                publisher,
                trust_score(features),
                json.dumps(features.to_dict(), ensure_ascii=False),
                r["rowid"],
            ))
        self._conn.executemany(
            "UPDATE items SET publisher=?, trust=?, trust_features_json=? WHERE rowid=?",
            updates,
        )
        self._conn.commit()
        return True

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
    def add_items(self, items: Iterable[ContentItem], run_id: Optional[int] = None,
                  tiering: str = "sections",
                  now: Optional[datetime] = None) -> int:
        """Insert content items; existing ids are skipped (append-only).

        Returns the number of newly stored rows. `tiering="marker"` stores the
        pre-P0 layering so the ablation harness can measure both arms against
        one corpus (see `claimable_of`). `now` pins the freshness feature: with
        the wall clock the same item scores differently on different days, so a
        harness or a test that quotes a trust number has to pass a fixed one.
        """
        rows = []
        for item in items:
            content = item.content or ""
            text_for_fp = f"{item.title}\n{content}"
            claimable = claimable_of(item, tiering)
            declared = item.sections[0].provenance if item.sections else "legacy_marker"
            confidence = item.sections[0].confidence if item.sections else None
            features = compute_features(
                source_type=item.source_type.value,
                text=claimable,
                author=item.author,
                provenance=declared,
                confidence=confidence,
                published_at=item.published_at,
                time_basis=item.time_basis,
                meta=item.metadata if isinstance(item.metadata, dict) else None,
                now=now,
            )
            rows.append(
                (
                    item.id,
                    item.source_type.value,
                    item.title,
                    item.locator,
                    item.author,
                    item.published_at.astimezone(timezone.utc).isoformat(),
                    item.fetched_at.astimezone(timezone.utc).isoformat(),
                    content,
                    claimable,
                    item.locator,
                    item.time_basis,
                    json.dumps([s.model_dump() for s in item.sections], ensure_ascii=False),
                    publisher_of(item.author, item.locator, str(item.url or "")),
                    trust_score(features),
                    json.dumps(features.to_dict(), ensure_ascii=False),
                    json.dumps(_jsonable(item.metadata), ensure_ascii=False),
                    _as_signed64(fingerprint(text_for_fp)),
                    run_id,
                )
            )
        before = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self._conn.executemany(
            """INSERT OR IGNORE INTO items
               (id, source_type, title, url, author, published_at, fetched_at,
                content, claimable, locator, time_basis, sections_json,
                publisher, trust, trust_features_json,
                metadata_json, fingerprint, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self._conn.commit()
        after = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        return int(after - before)

    def known_ids(self, ids: Iterable[str]) -> set:
        """Which of `ids` are already stored.

        Keeps a re-fetched, time-unknown item from being analysed again:
        `INSERT OR IGNORE` stops duplicate rows, but nothing else stops
        duplicate LLM spend.
        """
        wanted = [i for i in ids if i]
        if not wanted:
            return set()
        placeholders = ",".join("?" * len(wanted))
        rows = self._conn.execute(
            f"SELECT id FROM items WHERE id IN ({placeholders})", wanted
        ).fetchall()
        return {r["id"] for r in rows}

    # ---------------------------------------------------------------- read
    # Columns each search tier is allowed to look at. Closed set on purpose:
    # the names go into SQL text, so nothing user-controlled may reach here.
    _FTS_BY_TIER = {
        "all": ("items_fts", "content"),
        "claimable": ("claim_fts", "claimable"),
    }

    def search(
        self,
        query: str,
        limit: int = 20,
        tier: str = "all",
        source_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """FTS5 BM25-ranked full-text search over stored evidence.

        User text is sanitised into a conjunction of quoted phrases so FTS
        operators (NOT, OR, column filters, hyphens-as-`col`) can never
        leak into query syntax. Terms shorter than 3 chars can't be matched
        by the trigram tokenizer (common for CJK words like 工作/注册), so
        they fall back to an escaped LIKE scan.

        `tier` selects which authorship layer is searched: `all` (default, the
        panel's full-corpus view including comments and replies) or
        `claimable` (author-written text only — what evidence linking and
        claim grading must use).

        `source_types` narrows the search to one family of sources. The
        research loop uses this to look where it has not looked yet instead of
        re-asking the same slice of the corpus.
        """
        fts_table, body_column = self._FTS_BY_TIER.get(tier, self._FTS_BY_TIER["all"])
        scoped = ""
        params_filter: List[Any] = []
        if source_types:
            placeholders = ",".join("?" * len(source_types))
            scoped = f" AND i.source_type IN ({placeholders})"
            params_filter = list(source_types)
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
                f"""SELECT i.*, bm25({fts_table}) AS rank
                   FROM {fts_table}
                   JOIN items i ON i.rowid = {fts_table}.rowid
                   WHERE {fts_table} MATCH ?{scoped}
                   ORDER BY rank
                   LIMIT ?""",
                (match, *params_filter, limit),
            ).fetchall()
            for r in rows:
                d = self._row_to_dict(r)
                results[d["id"]] = d
        for term in like_terms:
            if len(results) >= limit:
                break
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            like_scoped = ""
            like_params: List[Any] = [like, like]
            if source_types:
                placeholders = ",".join("?" * len(source_types))
                like_scoped = f" AND source_type IN ({placeholders})"
                like_params += list(source_types)
            rows = self._conn.execute(
                f"""SELECT * FROM items
                   WHERE (title LIKE ? ESCAPE '\\' OR {body_column} LIKE ? ESCAPE '\\'){like_scoped}
                   ORDER BY published_at DESC LIMIT ?""",
                (*like_params, limit - len(results)),
            ).fetchall()
            for r in rows:
                d = self._row_to_dict(r)
                results.setdefault(d["id"], d)
        return list(results.values())[:limit]

    def source_families(self) -> List[str]:
        """Every source family present in the corpus, most items first."""
        rows = self._conn.execute(
            "SELECT source_type, COUNT(*) AS n FROM items GROUP BY source_type"
            " ORDER BY n DESC"
        ).fetchall()
        return [r["source_type"] for r in rows]

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
        if "sections_json" in d:
            try:
                d["sections"] = json.loads(d.pop("sections_json"))
            except (json.JSONDecodeError, TypeError):
                d["sections"] = []
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


def _parse_time(raw: Optional[str]) -> Optional[datetime]:
    """Read back an ISO timestamp, tolerating the formats older rows used."""
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
