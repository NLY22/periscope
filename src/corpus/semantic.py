"""Vector side of evidence retrieval, stored beside the lexical index.

Why this is optional and never the only leg: the pipeline must stay runnable
with no API key, and embeddings are the one part that cannot be computed
locally without shipping a model. So the semantic leg is an *additive* ranker —
lexical BM25 stays the spine, and RRF fuses the two when a vector index exists.

Vectors are keyed by model name: switching models leaves the old rows unusable
instead of silently mixing coordinate systems.
"""

from __future__ import annotations

import array
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .store import Corpus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    item_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);
"""


def encode(vector: Iterable[float]) -> bytes:
    return array.array("f", [float(v) for v in vector]).tobytes()


def decode(blob: bytes) -> List[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity; 0.0 when either side is degenerate."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na**0.5 * nb**0.5)


def rrf_merge(ranked_lists: List[List[str]], k: int = 60) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion over several ranked id lists.

    Chosen over score normalisation because BM25 ranks and cosine scores are
    not comparable magnitudes, but their *orders* are.
    """
    scores: Dict[str, float] = {}
    for one in ranked_lists:
        for rank, item_id in enumerate(one):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


class EmbeddingIndex:
    """Persistent item vectors for one embedding model."""

    def __init__(self, corpus: Corpus, model: str):
        self.model = model
        self._conn = corpus._conn
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def missing(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Items that have no vector for this model yet (author layer only)."""
        rows = self._conn.execute(
            """SELECT i.rowid AS rowid, i.id AS id, i.title AS title, i.claimable AS claimable
               FROM items i
               LEFT JOIN embeddings e ON e.item_id = i.id AND e.model = ?
               WHERE e.item_id IS NULL AND i.claimable != ''
               ORDER BY i.rowid DESC LIMIT ?""",
            (self.model, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def pending_total(self) -> int:
        return int(
            self._conn.execute(
                """SELECT COUNT(*) FROM items i
                   LEFT JOIN embeddings e ON e.item_id=i.id AND e.model=?
                   WHERE e.item_id IS NULL AND i.claimable != ''""",
                (self.model,),
            ).fetchone()[0]
        )

    def upsert(self, item_id: str, vector: List[float]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO embeddings (item_id, model, dim, vector, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (item_id, self.model, len(vector), encode(vector), _utc_now().isoformat()),
        )
        self._conn.commit()

    def search(self, query_vector: List[float], limit: int = 30) -> List[Tuple[str, float]]:
        rows = self._conn.execute(
            "SELECT item_id, vector FROM embeddings WHERE model=?", (self.model,)
        ).fetchall()
        scored = [(r["item_id"], cosine(query_vector, decode(r["vector"]))) for r in rows]
        scored = [s for s in scored if s[1] > 0.0]
        scored.sort(key=lambda kv: -kv[1])
        return scored[:limit]

    def count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM embeddings WHERE model=?", (self.model,)
            ).fetchone()[0]
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
