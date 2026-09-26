"""Ranking metrics for evidence retrieval: recall, precision, MRR, nDCG.

Standard information-retrieval measures, deliberately unmodified, so the
numbers in `docs/evaluation.md` are comparable with anything published
elsewhere rather than a metric invented for this repo.

Relevance is a mapping `item_id -> grade` per query (0 = not relevant).
Graded gains use 2**grade - 1, so a query with only binary labels behaves
exactly like the binary case.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

Run = Dict[str, List[str]]  # query id -> ranked item ids
QRel = Dict[str, Dict[str, int]]  # query id -> {item id: grade}


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    relevant = set(relevant)
    if not relevant:
        return 0.0
    hits = len([item_id for item_id in ranked[:k] if item_id in relevant])
    return hits / len(relevant)


def precision_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    if k <= 0:
        return 0.0
    relevant = set(relevant)
    hits = len([item_id for item_id in ranked[:k] if item_id in relevant])
    return hits / k


def reciprocal_rank(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    relevant = set(relevant)
    for position, item_id in enumerate(ranked, start=1):
        if item_id in relevant:
            return 1.0 / position
    return 0.0


def dcg(ranked: Sequence[str], grades: Dict[str, int], k: int) -> float:
    return sum(
        (2 ** grades.get(item_id, 0) - 1) / math.log2(position + 1)
        for position, item_id in enumerate(ranked[:k], start=1)
    )


def ndcg_at_k(ranked: Sequence[str], grades: Dict[str, int], k: int) -> float:
    ideal = sorted(grades.values(), reverse=True)[:k]
    ideal_dcg = sum(
        (2 ** grade - 1) / math.log2(position + 1)
        for position, grade in enumerate(ideal, start=1)
    )
    if ideal_dcg <= 0:
        return 0.0
    return dcg(ranked, grades, k) / ideal_dcg


def evaluate(
    runs: Run, qrels: QRel, ks: Tuple[int, ...] = (5, 10)
) -> Dict[str, float]:
    """Mean metrics over every query in `qrels` (queries absent from `runs`
    count as returning nothing, which is the honest reading of a crash)."""
    if not qrels:
        return {}
    totals: Dict[str, float] = {}
    count = len(qrels)
    for query_id, grades in qrels.items():
        ranked = runs.get(query_id, [])
        relevant = [item_id for item_id, grade in grades.items() if grade > 0]
        for k in ks:
            totals[f"recall@{k}"] = totals.get(f"recall@{k}", 0.0) + recall_at_k(ranked, relevant, k)
            totals[f"precision@{k}"] = totals.get(f"precision@{k}", 0.0) + precision_at_k(
                ranked, relevant, k
            )
            totals[f"ndcg@{k}"] = totals.get(f"ndcg@{k}", 0.0) + ndcg_at_k(ranked, grades, k)
        totals["mrr"] = totals.get("mrr", 0.0) + reciprocal_rank(ranked, relevant)
    return {key: round(value / count, 4) for key, value in sorted(totals.items())}
