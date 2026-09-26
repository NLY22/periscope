"""Hybrid evidence retrieval: expansion leg, vector leg, fusion, degradation.

The fake embedder is deliberately synonym-aware rather than a real model
stand-in: what these tests pin is the *mechanism* — that a vector leg can
surface an item no trigram matched, that fusion prefers items confirmed by both
legs, and that losing either leg silently falls back to lexical search.
Retrieval *quality* is measured by scripts/eval_retrieval.py, not here.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pytest

from src.corpus.retrieval import HybridRetriever
from src.corpus.semantic import EmbeddingIndex, cosine, decode, encode, rrf_merge
from src.corpus.store import Corpus
from src.models import ContentItem, SourceType
from src.research.session import ResearchSession, ResearchStore

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)

# Surface forms that mean the same thing; a real embedding model would place
# them close together, so the stub places them identically.
_SYNONYM_GROUPS: Dict[str, List[str]] = {
    "valuation": ["估值", "融资额", "valuation", "funding round"],
    "launch": ["发布", "上线", "released", "shipped"],
}


class StubEmbedder:
    def __init__(self):
        self.calls = 0

    async def embed(self, texts: List[str]) -> List[List[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> List[float]:
        lowered = text.lower()
        vector = [0.0] * 8
        for slot, (_, surface_forms) in enumerate(_SYNONYM_GROUPS.items()):
            if any(form.lower() in lowered for form in surface_forms):
                vector[slot * 2] = 1.0
        if not any(vector):  # unseen text still gets a stable, non-zero vector
            vector[-1] = 1.0
        return vector


class FailingEmbedder:
    async def embed(self, texts: List[str]) -> List[List[float]]:
        raise RuntimeError("endpoint down")


class StubExpander:
    """AIClient-compatible stub returning the JSON contract."""

    def __init__(self, terms: List[str]):
        self.terms = terms
        self.calls = 0

    async def complete(self, system: str, user: str, **kwargs) -> str:
        self.calls += 1
        return json.dumps({"terms": self.terms}, ensure_ascii=False)


def make_item(idx: str, title: str, content: str) -> ContentItem:
    return ContentItem(
        id=f"hyb:{idx}",
        source_type=SourceType.RSS,
        title=title,
        url=f"https://example.com/{idx}",
        content=content,
        author="tester",
        published_at=NOW,
        fetched_at=NOW,
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / "corpus.db")
    c.add_items(
        [
            make_item("paraphrase", "OpenForge 报道", "OpenForge 的估值达到三十亿。"),
            make_item("exact", "OpenForge news", "OpenForge funding round closed."),
            make_item("unrelated", "Other thing", "Nothing about markets here."),
        ]
    )
    return c


def run(corpus: Corpus, gather_query: str, limit: int = 5, **retriever_kwargs):
    return asyncio.run(
        HybridRetriever(corpus, **retriever_kwargs).gather(gather_query, limit=limit)
    )


# ------------------------------------------------------------------- fusion
def test_rrf_prefers_items_confirmed_by_both_legs() -> None:
    order = [item_id for item_id, _ in rrf_merge([["a", "b", "c"], ["b", "a", "d"]])]
    assert set(order[:2]) == {"a", "b"}
    assert order[-1] == "d"


def test_vectors_survive_the_sqlite_blob_roundtrip() -> None:
    vector = [0.5, -0.25, 1.0]
    assert cosine(vector, decode(encode(vector))) == pytest.approx(1.0)
    assert cosine(vector, []) == 0.0


# --------------------------------------------------------------------- legs
def test_expansion_leg_rescues_a_question_the_corpus_phrases_differently(
    corpus: Corpus,
) -> None:
    # 融资额/公司 appear nowhere in the corpus, so the lexical leg is empty.
    assert {row["id"] for row in run(corpus, "该公司的 B 轮 融资额")} == set()

    rescued = run(
        corpus,
        "该公司的 B 轮 融资额",
        expansion_client=StubExpander(["估值", "funding round"]),
        expansion_max_terms=2,
    )
    assert {row["id"] for row in rescued} == {"hyb:paraphrase", "hyb:exact"}
    assert "matched" in rescued[0]["retrieval"]


def test_vector_leg_finds_a_paraphrase_lexical_cannot(corpus: Corpus) -> None:
    index = EmbeddingIndex(corpus, "stub-model")
    embedder = StubEmbedder()
    retriever = HybridRetriever(corpus, index=index, embedder=embedder)
    assert asyncio.run(retriever.index_pending()) == 3
    assert embedder.calls >= 1

    # "valuation" is not a substring of the Chinese item, so only the vector
    # leg can reach it.
    lexical = {row["id"] for row in run(corpus, "valuation")}
    hybrid = {row["id"] for row in run(corpus, "valuation", index=index, embedder=StubEmbedder())}
    assert "hyb:paraphrase" not in lexical
    assert "hyb:paraphrase" in hybrid


def test_embedding_index_tracks_unindexed_items_per_model(corpus: Corpus) -> None:
    first = EmbeddingIndex(corpus, "model-a")
    assert first.pending_total() == 3
    assert len(first.missing(limit=2)) == 2
    asyncio.run(HybridRetriever(corpus, index=first, embedder=StubEmbedder()).index_pending())
    assert first.pending_total() == 0
    assert first.count() == 3
    # a different model must not reuse another model's coordinates
    assert EmbeddingIndex(corpus, "model-b").pending_total() == 3


def test_embedding_failure_degrades_to_lexical_only(corpus: Corpus) -> None:
    index = EmbeddingIndex(corpus, "stub-model")
    retriever = HybridRetriever(corpus, index=index, embedder=FailingEmbedder())
    assert asyncio.run(retriever.index_pending()) == 0
    rows = asyncio.run(retriever.gather("OpenForge funding round", limit=5))
    assert rows
    assert retriever.vector_leg is False


def test_no_optional_legs_means_the_free_path(corpus: Corpus) -> None:
    retriever = HybridRetriever(corpus)
    rows = asyncio.run(retriever.gather("funding round closed", limit=5))
    assert [row["id"] for row in rows] == ["hyb:exact"]
    assert retriever.expansion_calls == 0 and retriever.embedding_calls == 0


# ------------------------------------------------------------------- session
def test_session_uses_retriever_and_tags_provenance(corpus: Corpus) -> None:
    index = EmbeddingIndex(corpus, "stub-model")
    retriever = HybridRetriever(corpus, index=index, embedder=StubEmbedder())
    # investigate() indexes before asking; a direct gather call must do the same
    asyncio.run(retriever.index_pending())
    session = ResearchSession(
        store=ResearchStore(corpus),
        corpus=corpus,
        planner=None,
        claimable_only=True,
        retriever=retriever,
    )
    evidence = asyncio.run(session.gather_evidence_async("valuation"))
    assert any(e["id"] == "hyb:paraphrase" for e in evidence)
    assert all("retrieval" in e for e in evidence)


def test_session_without_retriever_matches_the_sync_core(corpus: Corpus) -> None:
    session = ResearchSession(
        store=ResearchStore(corpus), corpus=corpus, planner=None, claimable_only=True
    )
    assert asyncio.run(session.gather_evidence_async("funding round closed")) == (
        session.gather_evidence("funding round closed")
    )
