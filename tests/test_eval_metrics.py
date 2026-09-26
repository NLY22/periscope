"""Ranking metrics (hand-checked) and the retrieval ablation harness.

The harness test is the guard rail for docs/evaluation.md: tiering must change
what the corpus returns, and crowd-only items must disappear from the claimable
runs while staying reachable in the full-text view.
"""

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.corpus.metrics import (  # noqa: E402
    dcg,
    evaluate,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

import eval_retrieval as harness  # noqa: E402

# ------------------------------------------------------------------- metrics
def test_recall_and_precision_use_the_relevant_set_size_and_cutoff() -> None:
    ranked = ["a", "x", "b", "y", "z"]
    relevant = {"a", "b"}
    assert recall_at_k(ranked, relevant, k=2) == 0.5      # a only
    assert recall_at_k(ranked, relevant, k=3) == 1.0      # a and b
    assert precision_at_k(ranked, relevant, k=2) == 0.5   # a hits
    assert precision_at_k(ranked, relevant, k=5) == 0.4
    assert recall_at_k(ranked, set(), k=5) == 0.0


def test_reciprocal_rank_and_ndcg_match_hand_computed_values() -> None:
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0

    grades = {"a": 2, "b": 1}
    # ideal order is a(2) then b(1): gain 3/log2(2) + 1/log2(3)
    assert dcg(["a", "b"], grades, k=2) == pytest.approx(3 / 1 + 1 / 1.584962500721156)
    assert ndcg_at_k(["a", "b"], grades, k=2) == pytest.approx(1.0)
    assert ndcg_at_k(["b", "a"], grades, k=2) < 1.0
    assert ndcg_at_k(["x", "y"], grades, k=2) == 0.0


def test_evaluate_averages_over_every_query_in_qrels() -> None:
    runs = {"q1": ["a"], "q2": ["x", "b"]}
    qrels = {"q1": {"a": 2}, "q2": {"b": 2}}
    out = evaluate(runs, qrels, ks=(1,))
    assert out["recall@1"] == pytest.approx(0.5)
    assert out["mrr"] == pytest.approx((1.0 + 0.5) / 2)
    assert out["precision@1"] == pytest.approx(0.5)


def test_evaluate_counts_a_missing_run_as_returning_nothing() -> None:
    out = evaluate({"q1": ["a"]}, {"q1": {"a": 2}, "q2": {"b": 2}}, ks=(5,))
    assert out["recall@5"] == pytest.approx(0.5)


# ------------------------------------------------------------------- harness
@pytest.fixture(scope="module")
def fixture_corpus(tmp_path_factory):
    corpus = harness.build_corpus(tmp_path_factory.mktemp("eval"))
    yield corpus
    corpus.close()


def test_fixture_has_distractors_not_only_answers(fixture_corpus) -> None:
    total = fixture_corpus._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert total == 19  # 10 signal items + 9 look-alikes, so recall can drop


def _gather(fixture_corpus, query: str, tier: str, limit: int = 10):
    from src.corpus.retrieval import HybridRetriever

    retriever = HybridRetriever(fixture_corpus, tier=tier)
    return asyncio.run(retriever.gather(query, limit=limit))


def test_crowd_only_item_is_reachable_in_full_text_but_not_as_evidence(fixture_corpus) -> None:
    query = "OpenForge 估值 30 亿"
    full = {row["id"] for row in _gather(fixture_corpus, query, "all")}
    claimable = {row["id"] for row in _gather(fixture_corpus, query, "claimable")}
    assert "ev:forum-noise" in full          # a reply mentioned it
    assert "ev:forum-noise" not in claimable  # the author never did


def test_config_table_is_computed_for_every_query(fixture_corpus) -> None:
    queries = harness.load_queries()
    runs = asyncio.run(harness.run_config(fixture_corpus, harness.CONFIGS[0], queries, 10))
    assert set(runs) == {entry["id"] for entry in queries}
    metrics = harness.evaluate_metrics(runs, queries)
    for key in ("recall@5", "precision@5", "ndcg@10", "mrr"):
        assert 0.0 <= metrics[key] <= 1.0
    assert metrics["recall@10"] < 1.0  # distractors must be able to hide the answer
