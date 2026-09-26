"""Hybrid evidence retrieval: lexical spine + optional expansion and vectors.

`ResearchSession` and the evaluation harness both go through here, so the
retrieval policy that produces a report is the same one the metrics measure.

Cost model: BM25 is free and always runs; query expansion costs one cached LLM
call per distinct sub-question; the semantic leg costs one embedding call per
new item plus one per query. Both optional legs degrade to plain lexical search
when unavailable, which is what keeps the no-key path honest.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..ai.embeddings import EmbeddingError
from ..ai.expand import expand_query
from .semantic import EmbeddingIndex, rrf_merge
from .store import Corpus

logger = logging.getLogger(__name__)


def _discriminating_terms(text: str, max_terms: int, corpus: Corpus) -> List[str]:
    """Deferred import: the claim module owns term scoring, and importing it
    at module scope would make `corpus` depend on `analysis`."""
    from ..analysis.claims import discriminating_terms

    return discriminating_terms(text, max_terms=max_terms, corpus=corpus)


class HybridRetriever:
    """Evidence retrieval over the corpus for one query."""

    def __init__(
        self,
        corpus: Corpus,
        *,
        tier: str = "claimable",
        index: Optional[EmbeddingIndex] = None,
        embedder: Optional[Any] = None,
        expansion_client: Optional[Any] = None,
        expansion_max_terms: int = 4,
        semantic_top_k: int = 30,
        per_term_limit: int = 20,
        rrf_k: int = 60,
    ):
        self.corpus = corpus
        self.tier = tier
        self.index = index
        self.embedder = embedder
        self.expansion_client = expansion_client
        self.expansion_max_terms = expansion_max_terms
        self.semantic_top_k = semantic_top_k
        self.per_term_limit = per_term_limit
        self.rrf_k = rrf_k
        # observable, for tests and the eval harness
        self.expansion_calls = 0
        self.embedding_calls = 0
        self.vector_leg = False

    # ------------------------------------------------------------------ index
    async def index_pending(self, batch_size: int = 16) -> int:
        """Embed author-layer text for items with no vector yet.

        Returns how many rows were written. Any endpoint failure stops the
        pass and leaves the lexical spine in charge — a partial index is fine
        because unindexed items are simply unreachable by the vector leg.
        """
        if self.index is None or self.embedder is None:
            return 0
        rows = self.index.missing(limit=batch_size * 4)
        if not rows:
            return 0
        written = 0
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            texts = [f"{r['title']}\n{r['claimable']}"[:2000] for r in chunk]
            try:
                self.embedding_calls += 1
                vectors = await self.embedder.embed(texts)
            except (EmbeddingError, Exception) as exc:
                logger.warning("embedding indexing stopped: %s", exc)
                break
            for row, vector in zip(chunk, vectors):
                self.index.upsert(row["id"], vector)
                written += 1
        return written

    # ---------------------------------------------------------------- retrieve
    async def gather(
        self,
        query: str,
        limit: int,
        term_budget: int = 4,
        source_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fused evidence rows for one query, best rank first.

        `term_budget` is the widening dial: fewer terms means a looser query,
        which is what the research loop does when a precise question returns
        nothing. `source_types` restricts the search to families it has not
        tried yet.
        """
        terms = _discriminating_terms(
            query, max_terms=term_budget, corpus=self.corpus
        ) or [query]

        if self.expansion_client is not None:
            self.expansion_calls += 1
            extra = await expand_query(
                self.expansion_client, query, max_terms=self.expansion_max_terms
            )
            fresh = [t for t in extra if t.lower() not in {x.lower() for x in terms}]
            terms = terms + fresh[: self.expansion_max_terms]

        ranked_lists: List[List[str]] = []
        reasons: Dict[str, List[str]] = {}
        for term in terms:
            hits = [
                row["id"]
                for row in self.corpus.search(
                    term,
                    limit=self.per_term_limit,
                    tier=self.tier,
                    source_types=source_types,
                )
            ]
            if hits:
                ranked_lists.append(hits)
                for rank, item_id in enumerate(hits):
                    reasons.setdefault(item_id, []).append(f"lexical:{term}#{rank + 1}")

        if self.index is not None and self.embedder is not None:
            vector_hits = await self._vector_ranks(query)
            if vector_hits:
                ranked_lists.append(vector_hits)
                self.vector_leg = True
                for rank, item_id in enumerate(vector_hits):
                    reasons.setdefault(item_id, []).append(f"vector#{rank + 1}")

        if not ranked_lists:
            return []

        fused = rrf_merge(ranked_lists, k=self.rrf_k)[:limit]
        rows = self._rows_by_id([item_id for item_id, _ in fused])
        for row in rows:
            row["retrieval"] = {
                "terms": terms,
                "matched": reasons.get(row["id"], []),
                "vector_leg": self.vector_leg,
            }
        return rows

    async def _vector_ranks(self, query: str) -> List[str]:
        try:
            self.embedding_calls += 1
            vectors = await self.embedder.embed([query])
        except Exception as exc:
            logger.warning("query embedding failed, lexical only: %s", exc)
            return []
        if not vectors:
            return []
        return [item_id for item_id, _ in self.index.search(vectors[0], self.semantic_top_k)]

    def _rows_by_id(self, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.corpus._conn.execute(
            f"SELECT * FROM items WHERE id IN ({placeholders})", ids
        ).fetchall()
        by_id = {row["id"]: self.corpus._row_to_dict(row) for row in rows}
        return [by_id[i] for i in ids if i in by_id]
