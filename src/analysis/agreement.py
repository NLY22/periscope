"""Agreement between the claim grader and a human label.

The correctness layer currently has no measured accuracy — this is the
mechanism that produces it. Pure functions over `(human, machine)` pairs so
the maths is testable without a corpus, a model, or a network.

Labels follow the pipeline's own three-way verdict set. A FEVER-style
`not_enough_information` axis is deliberately *not* merged into
`unsupported`: in this pipeline `unsupported` means "the stored excerpts do not
confirm it", which is closer to insufficient evidence than to refutation, and
collapsing the two would quietly change what the number claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

LABELS: Tuple[str, ...] = ("supported", "contested", "unsupported")


@dataclass
class Agreement:
    n: int = 0
    accuracy: float = 0.0
    macro_f1: float = 0.0
    per_label: Dict[str, Dict[str, float]] = field(default_factory=dict)
    confusion: Dict[str, Dict[str, int]] = field(default_factory=dict)
    unlabeled: int = 0

    def table(self) -> str:
        header = "人工\\机器  " + "  ".join(f"{label:>11}" for label in LABELS)
        rows = [
            f"{human:<10}  " + "  ".join(f"{self.confusion.get(human, {}).get(m, 0):>11}" for m in LABELS)
            for human in LABELS
        ]
        metrics = "\n".join(
            f"- {label}: precision={self.per_label[label]['precision']:.3f} "
            f"recall={self.per_label[label]['recall']:.3f} f1={self.per_label[label]['f1']:.3f}"
            f" (gold={self.per_label[label]['gold']}, pred={self.per_label[label]['predicted']})"
            for label in LABELS
        )
        return (
            f"n={self.n}  accuracy={self.accuracy:.3f}  macro-F1={self.macro_f1:.3f}"
            + (f"  (跳过未标注 {self.unlabeled} 条)" if self.unlabeled else "")
            + f"\n{header}\n" + "\n".join(rows) + "\n" + metrics
        )


def score_pairs(pairs: Sequence[Tuple[str, str]]) -> Agreement:
    """Precision / recall / F1 per label plus macro-F1 and accuracy.

    `pairs` are `(human, machine)` verdict strings. Anything outside the
    three-label vocabulary is dropped, not guessed at.
    """
    usable = [(h, m) for h, m in pairs if h in LABELS and m in LABELS]
    agreement = Agreement(n=len(usable), unlabeled=len(pairs) - len(usable))
    if not usable:
        agreement.per_label = {
            label: {"precision": 0.0, "recall": 0.0, "f1": 0.0, "gold": 0, "predicted": 0}
            for label in LABELS
        }
        return agreement

    for human, machine in usable:
        agreement.confusion.setdefault(human, {}).setdefault(machine, 0)
        agreement.confusion[human][machine] += 1

    correct = sum(1 for h, m in usable if h == m)
    agreement.accuracy = correct / len(usable)

    f1s: List[float] = []
    for label in LABELS:
        tp = sum(1 for h, m in usable if h == label and m == label)
        gold = sum(1 for h, _ in usable if h == label)
        predicted = sum(1 for _, m in usable if m == label)
        precision = tp / predicted if predicted else 0.0
        recall = tp / gold if gold else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        agreement.per_label[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "gold": float(gold),
            "predicted": float(predicted),
        }
        f1s.append(f1)
    agreement.macro_f1 = sum(f1s) / len(f1s)
    return agreement


def independence_buckets(
    rows: Sequence[Tuple[int, str, str]]
) -> Dict[str, Dict[str, float]]:
    """Does "more independent sources" actually track human agreement?

    `rows` are `(independent_sources, human, machine)`. Bucketed as 1 / 2 / 3+
    so a monotone trend is visible — that trend is the evidence that the
    cluster-distinct counting is doing its job, and its absence is the evidence
    that crowd text or syndication is inflating it.
    """
    buckets = {"1": [0, 0], "2": [0, 0], "3+": [0, 0]}
    for sources, human, machine in rows:
        if human not in LABELS or machine not in LABELS:
            continue
        key = "1" if sources <= 1 else "2" if sources == 2 else "3+"
        buckets[key][0] += 1
        if human == machine:
            buckets[key][1] += 1
    return {
        key: {"n": float(count), "agreement": (hits / count) if count else 0.0}
        for key, (count, hits) in buckets.items()
    }
