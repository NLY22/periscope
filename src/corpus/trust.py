"""Item trust, independence, and the two verdict gates (P1).

Why a hand-weighted logistic model rather than a learned one: the fork's
promise is that widening sources keeps being *auditable*. A score a reader can
decompose into "源先验 / 作者等级 / 交叉支持 / 可核验实体 / 新鲜度 / 来源方式 −
模板度" answers "why was this treated as evidence"; a black box does not. The
weights are therefore explicit, and §5.3's human labels are used only to
calibrate them and to pick the thresholds — never to hide the features.

Two gates exist and they do different things (spec §1.6):

  triage   which claims are worth spending an LLM call on  -> T(claim) >= theta_triage
  verdict  what the answer is                             -> LLM grade + noisy-OR trust

`T` is a noisy-OR over the supporting items, not a sum. A sum has no ceiling,
so enough low-quality sources could push any claim through; that is the failure
mode widening sources actually invites.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..models import SOURCE_SPECS, SourceSpec

# Provenance discount (spec §5.1). `legacy_marker` is deliberately below
# `author`: its tier was inferred from a string convention, not declared.
PROVENANCE_FACTOR: Dict[str, float] = {
    "author": 1.0,
    "transcript": 0.95,
    "ocr": None,           # type: ignore[assignment] -> replaced by the section's own confidence
    "vlm": 0.6,
    "legacy_marker": 0.8,
    # A user export: the tier is declared, but Periscope did not fetch it and
    # cannot confirm the page said this. Above inferred history, below what we
    # pulled ourselves.
    "manual_export": 0.85,
}

_CJK = re.compile(r"[\u3400-\u9fff]")
_DIGIT_OR_UNIT = re.compile(r"\d|(?:亿|万|千亿|美元|美金|元|人民币|%)")
_URLISH = re.compile(r"https?://|www\.|\.com|\.org|\.cn", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class TrustWeights:
    """Coefficients of the logistic trust model. Calibration replaces them."""

    bias: float = -1.20
    prior: float = 1.10
    author_rank: float = 0.45
    cross_support: float = 0.55
    entity_checkable: float = 0.60
    freshness: float = 0.35
    provenance: float = 0.80
    template_penalty: float = 0.90


DEFAULT_WEIGHTS = TrustWeights()


SOURCE_PRIORS: Dict[str, float] = {spec.key: spec.credibility_prior for spec in SOURCE_SPECS}
_SOURCE_BY_KEY: Dict[str, SourceSpec] = {spec.key: spec for spec in SOURCE_SPECS}
_SAME_FAMILY_DISCOUNT = 0.5


def provenance_factor(provenance: str, confidence: Optional[float] = None) -> float:
    """How much of the author's voice this text actually carries."""
    if provenance == "ocr":
        return float(confidence) if confidence is not None else 0.5
    return PROVENANCE_FACTOR.get(provenance, 0.8)


def shingles(text: str, size: int = 5) -> List[str]:
    """Character 5-grams, CJK-friendly.

    Character (not word) shingles because the corpus is mixed Chinese/English
    and a segmenter would be a new dependency. This is the anti-templating
    signal: a marketing blurb reposted a hundred times shares almost all of it.
    """
    body = re.sub(r"\s+", " ", (text or "").strip().casefold())
    if len(body) < size:
        return [body] if body else []
    return [body[i: i + size] for i in range(len(body) - size + 1)]


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def template_score(text: str, peers: Sequence[str]) -> float:
    """Mean 5-gram Jaccard against the other items in the same cluster."""
    others = [p for p in peers if p and p.strip()]
    if not others:
        return 0.0
    mine = shingles(text)
    return max(jaccard(mine, shingles(p)) for p in others)


def entity_checkable(text: str) -> float:
    """Does the text name things a reader could go and verify?

    A claim that says "支持百万上下文" is checkable; "很多人觉得不错" is not.
    """
    body = text or ""
    signals = 0
    if re.search(r"[A-Za-z][A-Za-z0-9._+-]{2,}", body):
        signals += 1
    if _DIGIT_OR_UNIT.search(body):
        signals += 1
    if _URLISH.search(body):
        signals += 1
    if _ISO_DATE.search(body):
        signals += 1
    if _CJK.search(body) and len(body) > 40:
        signals += 1
    return min(1.0, signals / 3.0)


def author_rank(author: Optional[str], meta: Optional[Dict[str, Any]] = None) -> float:
    """Source-visible standing: forum level, verification, else neutral 0.5."""
    data = meta or {}
    level = data.get("author_level")
    if isinstance(level, (int, float)) and level >= 0:
        return max(0.0, min(1.0, 0.3 + float(level) / 20.0))
    if data.get("verified") or data.get("editorial_gate"):
        return 0.9
    return 0.5 if author else 0.4


def freshness(published_at: Optional[datetime], now: Optional[datetime] = None,
              half_life_days: float = 90.0) -> float:
    if published_at is None:
        return 0.5                     # unknown age is neutral, not trusted
    moment = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (moment - published_at).total_seconds() / 86400.0)
    return 0.5 ** (age_days / max(1e-6, half_life_days))


@dataclass
class TrustFeatures:
    """Every input to `trust_score`, kept so the score stays explainable."""

    source_type: str
    prior: float
    author_rank: float
    cross_support: float
    entity_checkable: float
    freshness: float
    provenance: float
    template: float
    cluster_size: int = 0
    time_basis: str = "published"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "prior": round(self.prior, 4),
            "author_rank": round(self.author_rank, 4),
            "cross_support": round(self.cross_support, 4),
            "entity_checkable": round(self.entity_checkable, 4),
            "freshness": round(self.freshness, 4),
            "provenance": round(self.provenance, 4),
            "template": round(self.template, 4),
            "cluster_size": self.cluster_size,
            "time_basis": self.time_basis,
        }

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "TrustFeatures":
        return TrustFeatures(
            source_type=str(raw.get("source_type") or "unknown"),
            prior=float(raw.get("prior", 0.5)),
            author_rank=float(raw.get("author_rank", 0.5)),
            cross_support=float(raw.get("cross_support", 0.0)),
            entity_checkable=float(raw.get("entity_checkable", 0.0)),
            freshness=float(raw.get("freshness", 0.5)),
            provenance=float(raw.get("provenance", 1.0)),
            template=float(raw.get("template", 0.0)),
            cluster_size=int(raw.get("cluster_size", 0)),
            time_basis=str(raw.get("time_basis") or "published"),
        )


def compute_features(
    *,
    source_type: str,
    text: str,
    author: Optional[str] = None,
    provenance: str = "author",
    confidence: Optional[float] = None,
    published_at: Optional[datetime] = None,
    time_basis: str = "published",
    peers: Sequence[str] = (),
    cluster_size: int = 0,
    meta: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> TrustFeatures:
    spec = _SOURCE_BY_KEY.get(source_type)
    prior = spec.credibility_prior if spec else 0.5
    support = min(1.0, cluster_size / 5.0)
    return TrustFeatures(
        source_type=source_type,
        prior=prior,
        author_rank=author_rank(author, meta),
        cross_support=support,
        entity_checkable=entity_checkable(text),
        freshness=freshness(published_at, now),
        provenance=provenance_factor(provenance, confidence),
        template=template_score(text, peers),
        cluster_size=cluster_size,
        time_basis=time_basis,
    )


def trust_score(features: TrustFeatures, weights: TrustWeights = DEFAULT_WEIGHTS) -> float:
    """σ(bias + Σ w·feature − w·template)."""
    z = (
        weights.bias
        + weights.prior * features.prior
        + weights.author_rank * features.author_rank
        + weights.cross_support * features.cross_support
        + weights.entity_checkable * features.entity_checkable
        + weights.freshness * features.freshness
        + weights.provenance * features.provenance
        - weights.template_penalty * features.template
    )
    return 1.0 / (1.0 + math.exp(-z))


# ------------------------------------------------------- independence + gates
def publisher_of(author: Optional[str], locator: str = "", url: str = "") -> Optional[str]:
    """Who published it. Missing publisher means the item casts no independence vote.

    Deliberately conservative: folding anonymous posts into one (type, NULL)
    bucket would let a pile of same-source spam read as independent corroboration.
    """
    name = (author or "").strip()
    if name:
        return name.lower()
    for candidate in (locator, url):
        match = re.search(r"(?:^|[/#@])u(?:ser)?/([^/?#&]+)", candidate or "")
        if match:
            return match.group(1).lower()
        match = re.match(r"@([\w.\-]+)", (candidate or "").strip())
        if match:
            return match.group(1).lower()
    return None


@dataclass
class Vote:
    source_type: str
    publisher: Optional[str]
    trust: float
    # The source's declared prior; None makes `classify` fall back to the
    # SOURCE_SPECS table, so a hand-built Vote keeps working.
    prior: Optional[float] = None
    # Near-duplicate group. Independence collapses per cluster first, so a
    # templated post recycled under one author is a single vote.
    cluster: Optional[str] = None


def distinct_publishers(votes: Iterable[Vote]) -> int:
    return len({
        (v.source_type, v.publisher) for v in votes if v.publisher
    })


def noisy_or(votes: Iterable[Vote]) -> float:
    """T(claim) = 1 − Π(1 − trust_i·d_i), d_i discounting same-family repeats."""
    residual = 1.0
    seen_families = set()
    seen_publishers = set()
    for vote in votes:
        if not vote.publisher:
            continue                       # casts no vote at all
        key = (vote.source_type, vote.publisher)
        if key in seen_publishers:
            continue
        seen_publishers.add(key)
        discount = 1.0 if vote.source_type not in seen_families else _SAME_FAMILY_DISCOUNT
        seen_families.add(vote.source_type)
        residual *= 1.0 - max(0.0, min(1.0, vote.trust)) * discount
    return 1.0 - residual


@dataclass(frozen=True)
class Thresholds:
    """Cut points for the two gates. Placeholders until the labels exist."""

    supported: float = 0.55
    triage: float = 0.30
    same_family_prior: float = 0.40
    same_family_publishers: int = 3


def classify(T: float, votes: Sequence[Vote], thresholds: Thresholds = Thresholds(),
             *, contradicted: bool = False) -> str:
    """`supported` needs either cross-family breadth or deep same-family corroboration.

    The second branch is what keeps "only one forum had this" from being
    permanently un-supportable, which would contradict the point of widening.
    """
    if contradicted:
        return "contested"
    by_family: Dict[str, List[Vote]] = {}
    for vote in votes:
        if vote.publisher:
            by_family.setdefault(vote.source_type, []).append(vote)
    cross_family = len(by_family) >= 2
    deep_single = False
    if len(by_family) == 1:
        family, members = next(iter(by_family.items()))
        prior = members[0].prior
        if prior is None:
            prior = SOURCE_PRIORS.get(family, 0.0)
        deep_single = (
            len({v.publisher for v in members}) >= thresholds.same_family_publishers
            and prior >= thresholds.same_family_prior
        )
    return "supported" if (T >= thresholds.supported and (cross_family or deep_single)) \
        else "unsupported"


def roc_thresholds(pairs: Sequence[tuple]) -> Optional[Thresholds]:
    """Pick (supported, triage) from labelled (trust, is_supported) pairs.

    Returns None when there is nothing to calibrate on — the caller must then
    keep the defaults and say so, rather than shipping numbers that look
    fitted but were not.
    """
    scored = [(t, bool(y)) for t, y in pairs if t is not None]
    if len(scored) < 2 or len({y for _, y in scored}) < 2:
        return None
    grid = [i / 100.0 for i in range(1, 100)]
    best_f, best_theta = -1.0, Thresholds().supported
    for theta in grid:
        tp = sum(1 for t, y in scored if y and t >= theta)
        fp = sum(1 for t, y in scored if not y and t >= theta)
        fn = sum(1 for t, y in scored if y and t < theta)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best_f:
            best_f, best_theta = f1, theta
    triage = max(0.01, min(best_theta - 0.15, best_theta))
    return Thresholds(supported=round(best_theta, 2), triage=round(triage, 2))
