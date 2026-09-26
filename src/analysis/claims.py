"""Claim-level correctness analysis (Periscope Phase C).

Horizon answers "what is worth reading today". Periscope must also answer
"is it true, and how independently is it supported" — which requires
breaking prose into atomic claims and matching each claim against the
evidence corpus.

Pipeline (the shape of academic claim-verification work such as
OpenFactVerification, simplified to run on a free API tier):

1. extract_claims(item)  — LLM turns one ContentItem into atomic, checkable
   claims (opinions/advice dropped).
2. link_evidence(claim)  — deterministic: FTS over the corpus finds items
   mentioning the claim. No LLM, so this runs cheaply every cycle.
3. recompute_independence — cluster-distinct evidence count: ten
   syndicated copies of one press release are ONE vote, which is the whole
   point of "many sources" vs "one source repeated".
4. grade_claim(claim)    — LLM labels supported / contested / unsupported,
   but only for claims that already have >= N independent sources, and only
   up to a per-run call budget.

Design constraints from the Agnes free tier (slow, rate-limited):
- LLM is only touched in steps 1 and 4; both degrade to "skipped".
- Every run bounds its own LLM cost; leftover claims stay `linked` and are
  picked up next run because the corpus is persistent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..ai.utils import parse_json_response
from ..corpus.sections import claimable_text
from ..corpus.store import Corpus
from ..models import ContentItem

logger = logging.getLogger(__name__)

VERDICTS = {"supported", "contested", "unsupported"}
CLAIM_TYPES = {"fact", "prediction"}

# Terms too generic to discriminate between claims; kept small on purpose —
# evidence linking prefers recall, the grader decides precision.
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has",
    "was", "were", "will", "are", "its", "our", "you", "your", "not",
    "but", "all", "can", "new", "says", "said", "about", "after", "before",
    "因为", "所以", "这个", "那个", "我们", "他们", "可以", "已经", "就是",
    "不是", "什么", "怎么", "觉得", "感觉", "一下", "目前", "相关", "表示",
}


@dataclass
class Claim:
    """One atomic, checkable statement distilled from a corpus item."""

    id: str
    item_id: str
    text: str
    claim_type: str = "fact"
    time_scope: Optional[str] = None
    status: str = "extracted"  # extracted | linked | graded
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    evidence_ids: List[str] = field(default_factory=list)
    independent_sources: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "item_id": self.item_id,
            "text": self.text,
            "claim_type": self.claim_type,
            "time_scope": self.time_scope,
            "status": self.status,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "independent_sources": self.independent_sources,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class EvidenceLink:
    """A corpus item linked to one claim."""

    claim_id: str
    item_id: str
    cluster_id: Optional[str]
    source_type: str
    score: float


class ClaimStore:
    """Claims and evidence links, stored inside the corpus database.

    Sharing the Corpus connection lets a single SQL join resolve "how many
    independent sources support this claim" — clusters collapse duplicates.
    """

    _SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    text TEXT NOT NULL,
    claim_type TEXT NOT NULL DEFAULT 'fact',
    time_scope TEXT,
    status TEXT NOT NULL DEFAULT 'extracted',
    verdict TEXT,
    confidence REAL,
    independent_sources INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_item ON claims(item_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    cluster_id TEXT,
    source_type TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (claim_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON claim_evidence(claim_id);
"""

    def __init__(self, corpus: Corpus):
        self._conn = corpus._conn
        self._conn.executescript(self._SCHEMA)
        self._conn.commit()

    # ---------------------------------------------------------------- write
    def upsert_claims(self, claims: List[Claim]) -> int:
        inserted = 0
        for c in claims:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO claims
                   (id, item_id, text, claim_type, time_scope, status, verdict,
                    confidence, independent_sources, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    c.id, c.item_id, c.text, c.claim_type, c.time_scope,
                    c.status, c.verdict, c.confidence,
                    c.independent_sources, c.created_at.isoformat(),
                ),
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    def add_evidence(self, links: List[EvidenceLink]) -> None:
        self._conn.executemany(
            """INSERT OR REPLACE INTO claim_evidence
               (claim_id, item_id, cluster_id, source_type, score)
               VALUES (?, ?, ?, ?, ?)""",
            [(l.claim_id, l.item_id, l.cluster_id, l.source_type, l.score) for l in links],
        )
        self._conn.commit()

    def set_status(self, claim_id: str, status: str, **fields: Any) -> None:
        cols = ["status=?"]
        vals: List[Any] = [status]
        for key, value in fields.items():
            cols.append(f"{key}=?")
            vals.append(value)
        vals.append(claim_id)
        self._conn.execute(
            f"UPDATE claims SET {', '.join(cols)} WHERE id=?", vals
        )
        self._conn.commit()

    # ----------------------------------------------------------------- read
    def get_claim(self, claim_id: str) -> Optional[Claim]:
        row = self._conn.execute(
            "SELECT * FROM claims WHERE id=?", (claim_id,)
        ).fetchone()
        return self._row_to_claim(row) if row else None

    def claims_by_status(self, status: str, limit: int = 100) -> List[Claim]:
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE status=? ORDER BY created_at LIMIT ?",
            (status, limit),
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    def claims_for_item(self, item_id: str) -> List[Claim]:
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE item_id=? ORDER BY id", (item_id,)
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    def evidence_for(self, claim_id: str, limit: int = 8) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT e.item_id, e.cluster_id, e.source_type, e.score,
                      i.title, i.url, i.published_at
               FROM claim_evidence e JOIN items i ON i.id = e.item_id
               WHERE e.claim_id=? ORDER BY e.score DESC LIMIT ?""",
            (claim_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def recompute_independence(self) -> int:
        """Set independent_sources = cluster-distinct evidence count."""
        rows = self._conn.execute(
            """SELECT claim_id,
                      COUNT(DISTINCT COALESCE(cluster_id, item_id)) AS n
               FROM claim_evidence GROUP BY claim_id"""
        ).fetchall()
        updated = 0
        for r in rows:
            cur = self._conn.execute(
                """UPDATE claims SET independent_sources=?
                   WHERE id=? AND (independent_sources!=? OR status='extracted')""",
                (r["n"], r["claim_id"], r["n"]),
            )
            updated += cur.rowcount
        self._conn.commit()
        return updated

    def pending_grading(self, min_sources: int, limit: int) -> List[Claim]:
        rows = self._conn.execute(
            """SELECT * FROM claims
               WHERE status='linked' AND independent_sources>=?
               ORDER BY independent_sources DESC, created_at LIMIT ?""",
            (min_sources, limit),
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    def stats(self) -> Dict[str, Any]:
        total = self._conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        by_status = dict(
            self._conn.execute(
                "SELECT status, COUNT(*) FROM claims GROUP BY status"
            ).fetchall()
        )
        by_verdict = dict(
            self._conn.execute(
                "SELECT verdict, COUNT(*) FROM claims WHERE verdict IS NOT NULL GROUP BY verdict"
            ).fetchall()
        )
        multi = self._conn.execute(
            "SELECT COUNT(*) FROM claims WHERE independent_sources>=2"
        ).fetchone()[0]
        return {
            "claims": total,
            "by_status": by_status,
            "by_verdict": by_verdict,
            "multi_source": multi,
        }

    @staticmethod
    def _row_to_claim(row) -> Claim:
        return Claim(
            id=row["id"],
            item_id=row["item_id"],
            text=row["text"],
            claim_type=row["claim_type"],
            time_scope=row["time_scope"],
            status=row["status"],
            verdict=row["verdict"],
            confidence=row["confidence"],
            independent_sources=row["independent_sources"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


# --------------------------------------------------------------- tokenizing

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+#-]*|[\u4e00-\u9fff]+")

# Characters that carry grammar rather than topic. A candidate n-gram stuffed
# with them ("了什么", "哪些发") is punctuation-shaped noise: it matches almost
# nothing useful and drowns the real terms.
_CJK_FUNCTION = set(
    "的了是在和与或就都也很更被把从对为以于之及其并等这那们我你他她它有没有不"
    "么吗呢吧啊呀哦哪些什么怎样如何并且但因为如果所一二三四五六七八九十几半全"
)


def _penalty(gram: str) -> int:
    return sum(1 for ch in gram if ch in _CJK_FUNCTION)


def _cjk_candidates(run: str) -> List[str]:
    """Sliding 2- and 3-grams of one uninterrupted CJK run.

    3-grams are what FTS5's trigram tokenizer can match; 2-grams are kept as
    well because most real Chinese words are exactly two characters and the
    corpus falls back to LIKE for terms that short.
    """
    if len(run) <= 2:
        return [run]
    out = [run[i : i + 3] for i in range(len(run) - 2)]
    out += [run[i : i + 2] for i in range(len(run) - 1)]
    return out


def _term_candidates(text: str) -> List[str]:
    """All searchable terms in order of first appearance, deduped."""
    out: List[str] = []
    seen = set()
    for token in _TOKEN_RE.findall(text.lower()):
        grams = _cjk_candidates(token) if token[0] >= "\u4e00" else [token]
        for gram in grams:
            if gram in seen or gram in _STOPWORDS or len(gram) < 2:
                continue
            # a 3-gram with two function chars ("了什么") is 90% noise
            if _penalty(gram) > (1 if len(gram) == 2 else 2):
                continue
            seen.add(gram)
            out.append(gram)
    return out


def discriminating_terms(
    text: str,
    max_terms: int = 4,
    corpus: Optional[Corpus] = None,
) -> List[str]:
    """Pick the terms a corpus search should use to find this claim's topic.

    The full claim sentence is a bad FTS query (too many common words); names,
    numbers and content-word n-grams carry the signal. Returns lowercase terms,
    best first, no term contained in another.

    With a `corpus`, terms are scored by measured document frequency: ones that
    match nothing are useless, ones that match nearly everything (发布, "the")
    retrieve noise, so the sweet spot is rare-but-present, content-only n-grams.
    Without a store (unit tests, pre-fetch planning) it degrades to shape
    heuristics: fewest function characters, then longest, then earliest.
    """
    candidates = _term_candidates(text)
    if not candidates:
        return []
    if corpus is None:
        ranked = sorted(
            candidates,
            key=lambda g: (_penalty(g), -len(g), candidates.index(g)),
        )
    else:
        total = corpus.stats()["items"]
        ceiling = max(2, total // 5)  # present in >20% of corpus = generic
        freq = {g: corpus.document_frequency(g) for g in candidates}
        ranked = sorted(
            candidates,
            key=lambda g: (
                freq[g] == 0,  # can't retrieve anything
                _penalty(g),
                freq[g] > ceiling,  # retrieves everything
                -freq[g],
                -len(g),
            ),
        )
    chosen: List[str] = []
    for term in ranked:
        # overlapping n-grams ("大模型" / "模型" / "开源大") retrieve the same
        # rows; spending a slot on each is pure waste.
        if any(term in c or c in term for c in chosen):
            continue
        chosen.append(term)
        if len(chosen) == max_terms:
            break
    return chosen


# ------------------------------------------------------------------ prompts

EXTRACT_SYSTEM = """\
You extract claims for a fact-analysis pipeline.

Rules:
- A claim is one atomic, checkable statement of fact (or an explicit
  prediction). Drop opinions, advice, questions and pure sentiment.
- Under 30 words, self-contained (resolve pronouns), written in the
  language of the source. Keep names, numbers and dates exactly as given.
- Return ONLY a JSON object, nothing else:
  {"claims": [{"text": "...", "type": "fact", "time_scope": "2026-09"}]}
  type is "fact" or "prediction"; time_scope may be null.
  If nothing is checkable, return {"claims": []}."""

GRADE_SYSTEM = """\
You judge one CLAIM against evidence excerpts from independent sources.
Use ONLY the given excerpts — never outside knowledge.

- "supported": the sources agree on the claim's specifics.
- "contested": at least one source materially contradicts the claim.
- "unsupported": excerpts are too vague or off-topic to confirm it.
If excerpts disagree, prefer "contested" over "supported".

Return ONLY a JSON object, nothing else:
{"verdict": "supported", "confidence": 0.8, "reason": "short reason"}"""


def claim_id_for(item_id: str, index: int, text: str) -> str:
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text.lower()).strip("-")[:32]
    return f"claim:{item_id}:{index}:{slug}"


class ClaimAnalyzer:
    """Extraction + linking + grading over the persistent corpus."""

    def __init__(
        self,
        store: ClaimStore,
        corpus: Corpus,
        client: Optional[Any] = None,  # AIClient-compatible; None = deterministic only
        max_claims_per_item: int = 5,
        evidence_per_claim: int = 6,
        grade_min_sources: int = 2,
        content_chars: int = 3500,
        claimable_only: bool = True,
    ):
        self.store = store
        self.corpus = corpus
        self.client = client
        self.max_claims_per_item = max_claims_per_item
        self.evidence_per_claim = evidence_per_claim
        self.grade_min_sources = grade_min_sources
        self.content_chars = content_chars
        # Ablation knob for the evaluation harness: with False the pipeline
        # behaves like Phase C and treats comment/reply text as evidence.
        self.claimable_only = claimable_only
        self.llm_calls = 0  # observable budget accounting for tests/UI

    @property
    def _search_tier(self) -> str:
        return "claimable" if self.claimable_only else "all"

    @property
    def llm_available(self) -> bool:
        return self.client is not None

    # ---------------------------------------------------------- 1. extract
    async def extract_claims(self, item: ContentItem) -> List[Claim]:
        """Distill one item into persisted claims ([] when no LLM/no text)."""
        body = self._author_text(item.content)
        if not body or self.client is None:
            return []
        user = (
            f"标题: {item.title}\n"
            f"来源: {item.source_type.value} ({item.url})\n"
            f"发布时间: {item.published_at.date()}\n\n"
            f"正文:\n{body[: self.content_chars]}"
        )
        self.llm_calls += 1
        try:
            response = await self.client.complete(system=EXTRACT_SYSTEM, user=user)
        except Exception as exc:
            logger.warning("claim extraction failed for %s: %s", item.id, exc)
            return []
        parsed = parse_json_response(response)
        if not isinstance(parsed, dict):
            logger.warning("unparseable extraction response for %s", item.id)
            return []

        claims: List[Claim] = []
        seen: List[str] = []
        for raw in parsed.get("claims", []):
            if len(claims) >= self.max_claims_per_item:
                break
            if not isinstance(raw, dict):
                continue
            text = " ".join(str(raw.get("text", "")).split())
            if len(text) < 6:
                continue
            ctype = raw.get("type")
            if ctype not in CLAIM_TYPES:
                ctype = "fact"
            dedup_key = re.sub(r"[\W_]+", "", text.lower())
            if any(_same_statement(dedup_key, s) for s in seen):
                continue
            seen.append(dedup_key)
            scope = raw.get("time_scope")
            claims.append(
                Claim(
                    id=claim_id_for(item.id, len(claims), text),
                    item_id=item.id,
                    text=text,
                    claim_type=ctype,
                    time_scope=str(scope).strip() if scope else None,
                )
            )
        self.store.upsert_claims(claims)
        return claims

    # ----------------------------------------------------------- 2. link
    def link_evidence(self, claim: Claim) -> List[EvidenceLink]:
        """Deterministically attach corpus items that mention the claim."""
        terms = discriminating_terms(claim.text, corpus=self.corpus)
        rows: List[Dict[str, Any]] = []
        for size in (len(terms), 3, 2, 1):
            if size == 0:
                break
            query = " ".join(terms[: min(size, len(terms))])
            rows = self.corpus.search(
                query, limit=self.evidence_per_claim + 4, tier=self._search_tier
            )
            if rows:
                break
        # Keep only items that actually share signal with the claim, and
        # never drop the origin item: it is the assertion's provenance.
        links: List[EvidenceLink] = []
        seen_ids = set()
        for row in rows:
            if row["id"] in seen_ids:
                continue
            seen_ids.add(row["id"])
            links.append(
                EvidenceLink(
                    claim_id=claim.id,
                    item_id=row["id"],
                    cluster_id=row.get("cluster_id"),
                    source_type=row["source_type"],
                    score=-float(row.get("rank", 0.0)),
                )
            )
            if len(links) >= self.evidence_per_claim:
                break
        if claim.item_id not in seen_ids:
            origin = self.corpus._conn.execute(
                "SELECT id, source_type, cluster_id FROM items WHERE id=?",
                (claim.item_id,),
            ).fetchone()
            if origin is not None:
                links.insert(
                    0,
                    EvidenceLink(
                        claim_id=claim.id,
                        item_id=origin["id"],
                        cluster_id=origin["cluster_id"],
                        source_type=origin["source_type"],
                        score=float("inf"),
                    ),
                )
        links = links[: self.evidence_per_claim]
        if links:
            self.store.add_evidence(links)
            self.store.set_status(claim.id, "linked")
            claim.status = "linked"
            claim.evidence_ids = [l.item_id for l in links]
        return links

    def link_all_pending(self) -> int:
        """Link every freshly extracted claim; returns how many got evidence."""
        linked = 0
        for claim in self.store.claims_by_status("extracted", limit=500):
            if self.link_evidence(claim):
                linked += 1
        self.store.recompute_independence()
        return linked

    # ----------------------------------------------------------- 4. grade
    async def grade_claim(self, claim: Claim) -> Optional[Claim]:
        if self.client is None:
            return None
        evidence = self.store.evidence_for(claim.id)
        if not evidence:
            return None
        lines = []
        for e in evidence:
            body = self._excerpt(e["item_id"])
            tag = "origin" if e["item_id"] == claim.item_id else "independent"
            lines.append(f"- [{e['source_type']}|{tag}] {e['title']}: {body}")
        user = (
            f"CLAIM: {claim.text}\n"
            f"时间范围: {claim.time_scope or '未知'}\n\n"
            "证据摘录:\n" + "\n".join(lines)
        )
        self.llm_calls += 1
        try:
            response = await self.client.complete(system=GRADE_SYSTEM, user=user)
        except Exception as exc:
            logger.warning("grading failed for %s: %s", claim.id, exc)
            return None
        parsed = parse_json_response(response)
        if not isinstance(parsed, dict) or parsed.get("verdict") not in VERDICTS:
            logger.warning("unusable grade for %s", claim.id)
            return None
        try:
            confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        self.store.set_status(
            claim.id, "graded", verdict=parsed["verdict"], confidence=confidence
        )
        claim.status = "graded"
        claim.verdict = parsed["verdict"]
        claim.confidence = confidence
        return claim

    async def grade_pending(self, max_calls: int = 10) -> int:
        """Grade well-sourced claims, bounded by max_calls LLM invocations.

        Failed grades still consume budget (the API was called) but leave
        the claim `linked` for a later run.
        """
        if self.client is None:
            return 0
        graded = 0
        skipped: set[str] = set()
        for _ in range(max_calls):
            pending = [
                c
                for c in self.store.pending_grading(self.grade_min_sources, limit=max_calls)
                if c.id not in skipped
            ]
            if not pending:
                break
            claim = pending[0]
            result = await self.grade_claim(claim)
            if result is None:
                skipped.add(claim.id)
                continue
            graded += 1
        return graded

    def _author_text(self, content: Optional[str]) -> str:
        """Author-written text of an item body.

        A reply in a comment thread is somebody's opinion, not the item's
        assertion; feeding it to extraction turns crowd noise into claims.
        """
        text = content or ""
        return claimable_text(text) if self.claimable_only else text.strip()

    def _excerpt(self, item_id: str, chars: int = 400) -> str:
        row = self.corpus._conn.execute(
            "SELECT content, claimable FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            return ""
        source = row["claimable"] if self.claimable_only else row["content"]
        return re.sub(r"\s+", " ", source or "")[:chars]


def _bigrams(s: str) -> set:
    return {s[i : i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _same_statement(a: str, b: str) -> bool:
    """Duplicate test for one item's claim list: same characters shuffled.

    Claims are short, so SimHash is noisy; character-bigram containment is
    exact, cheap, and language-agnostic (works for zh runs and latin).
    """
    if a == b:
        return True
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return False
    return len(ba & bb) / min(len(ba), len(bb)) >= 0.85
