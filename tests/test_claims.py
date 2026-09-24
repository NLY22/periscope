"""Tests for the claim-analysis layer (Phase C).

The LLM is always faked here: extraction/grading contracts are pinned by
fixture responses, and the deterministic half (linking, independence
counting, budgets) is what these tests actually police.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from src.analysis.claims import (
    Claim,
    ClaimAnalyzer,
    ClaimStore,
    EvidenceLink,
    discriminating_terms,
)
from src.corpus.store import Corpus
from src.models import ContentItem, SourceType

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


class FakeLLM:
    """Queued responses keyed by call order; records prompts."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, **kwargs) -> str:
        self.prompts.append((system, user))
        if not self.responses:
            raise AssertionError("FakeLLM out of responses")
        out = self.responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def make_item(idx: str, title: str, content: str, source=SourceType.V2EX) -> ContentItem:
    return ContentItem(
        id=f"test:{idx}",
        source_type=source,
        title=title,
        url=f"https://example.com/{idx}",
        content=content,
        author="t",
        published_at=NOW,
        fetched_at=NOW,
    )


def extract_response(*claims) -> str:
    return json.dumps(
        {
            "claims": [
                {"text": c, "type": "fact", "time_scope": None} for c in claims
            ]
        },
        ensure_ascii=False,
    )


@pytest.fixture()
def corpus(tmp_path):
    c = Corpus(tmp_path / "analysis.db")
    yield c
    c.close()


# ----------------------------------------------------------- term selection
def test_discriminating_terms_prefers_long_rare_tokens() -> None:
    terms = discriminating_terms("OpenAI 发布 GPT-6，支持两百万 token 上下文窗口")
    assert "openai" in terms or "gpt-6" in terms
    # generic connector words must not squeeze out signal
    assert all(t not in ("因为", "可以", "the") for t in terms)


def test_discriminating_terms_cjk_runs() -> None:
    terms = discriminating_terms("上下文窗口扩展到两百万")
    assert any(len(t) >= 2 for t in terms)


# --------------------------------------------------------------- extraction
def test_extract_claims_persists_and_dedupes(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    llm = FakeLLM(
        [
            extract_response(
                "OpenAI 于 2026 年 9 月发布 GPT-6。",
                "GPT-6 支持两百万 token 上下文。",
                "OpenAI 于 2026 年 9 月发布了 GPT-6。",  # near-dup by containment
                "太棒了！",  # too short -> dropped
            )
        ]
    )
    analyzer = ClaimAnalyzer(store, corpus, client=llm)
    item = make_item("i1", "GPT-6 发布", "OpenAI 昨天正式发布 GPT-6，支持两百万 token 上下文窗口。")
    claims = asyncio.run(analyzer.extract_claims(item))
    assert len(claims) == 2
    assert store.claims_for_item("test:i1")  # persisted
    assert analyzer.llm_calls == 1


def test_extract_claims_no_client_degrades(corpus: Corpus) -> None:
    analyzer = ClaimAnalyzer(ClaimStore(corpus), corpus, client=None)
    item = make_item("i2", "t", "content body here")
    assert asyncio.run(analyzer.extract_claims(item)) == []


def test_extract_claims_llm_error_swallowed(corpus: Corpus) -> None:
    llm = FakeLLM([RuntimeError("rate limited")])
    analyzer = ClaimAnalyzer(ClaimStore(corpus), corpus, client=llm)
    item = make_item("i3", "t", "some content to extract")
    assert asyncio.run(analyzer.extract_claims(item)) == []  # no raise


def test_extract_respects_max_claims(corpus: Corpus) -> None:
    llm = FakeLLM([extract_step("c", 9)])
    analyzer = ClaimAnalyzer(ClaimStore(corpus), corpus, client=llm, max_claims_per_item=5)
    item = make_item("i4", "t", "long content " * 50)
    claims = asyncio.run(analyzer.extract_claims(item))
    assert len(claims) == 5


def extract_step(prefix: str, n: int) -> str:
    return extract_response(*[f"{prefix}{i} 发布了版本 {i} 支持功能 {i}" for i in range(n)])


# ------------------------------------------------------------------- linking
def test_link_groups_clustered_duplicates(corpus: Corpus) -> None:
    """Two syndicated copies of one article = ONE independent source."""
    body = (
        "DeepSeek 发布新模型 DeepSeek-V4，上下文扩展到百万级。"
        "官方博客称其代码能力超过上一代，评测得分 92 分。"
        "开发者社区反响热烈，多家媒体跟进报道此事。"
    )
    corpus.add_items(
        [
            make_item("a", "DeepSeek-V4 发布", body, SourceType.V2EX),
            make_item("b", "DeepSeek-V4 发布", body, SourceType.RSS),  # copy
            make_item(
                "c",
                "无关内容",
                "今天天气很好适合出门散步，商场打折活动很多。",
                SourceType.RSS,
            ),
        ]
    )
    corpus.recompute_clusters(max_distance=3)
    store = ClaimStore(corpus)
    claim = Claim(
        id="claim:test:x:0:deepseek-v4",
        item_id="test:a",
        text="DeepSeek 发布 DeepSeek-V4，上下文扩展到百万级",
    )
    store.upsert_claims([claim])
    analyzer = ClaimAnalyzer(store, corpus, client=None)
    links = analyzer.link_evidence(claim)
    assert links, "should find evidence"
    analyzer.store.recompute_independence()
    stored = store.get_claim(claim.id)
    assert stored.status == "linked"
    # both copies + origin found, but independence counts clusters
    linked_ids = {l.item_id for l in links}
    assert {"test:a", "test:b"} <= linked_ids
    assert stored.independent_sources == 1  # clustered: one vote


def test_link_counts_independent_sources(corpus: Corpus) -> None:
    # same topic, different write-ups that SimHash keeps apart
    corpus.add_items(
        [
            make_item(
                "a",
                "DeepSeek-V4 发布",
                "DeepSeek 于 9 月 20 日发布 DeepSeek-V4 模型，主打代码能力与百万上下文。",
                SourceType.V2EX,
            ),
            make_item(
                "b",
                "行业观察",
                "9 月 20 日，DeepSeek-V4 模型上线，多位工程师实测其代码补全延迟约 30ms。",
                SourceType.RSS,
            ),
        ]
    )
    corpus.recompute_clusters(max_distance=3)
    store = ClaimStore(corpus)
    claim = Claim(
        id="claim:test:a:0:release",
        item_id="test:a",
        text="DeepSeek-V4 于 9 月 20 日发布",
    )
    store.upsert_claims([claim])
    analyzer = ClaimAnalyzer(store, corpus, client=None)
    analyzer.link_evidence(claim)
    analyzer.store.recompute_independence()
    stored = store.get_claim(claim.id)
    assert stored.independent_sources >= 2


# -------------------------------------------------------------------- budget
def test_grade_pending_respects_client_and_budget(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    corpus.add_items(
        [
            make_item("g1", "来源一", "某个模型发布版本内容甲", SourceType.V2EX),
            make_item("g2", "来源二", "某个模型发布版本内容乙", SourceType.RSS),
        ]
    )
    for i in range(5):
        claim = Claim(
            id=f"claim:test:g{i}:0:t{i}",
            item_id="test:g1",
            text=f"某个模型发布版本 {i}",
        )
        store.upsert_claims([claim])
        # two rows in distinct clusters => 2 independent sources
        store.add_evidence(
            [
                EvidenceLink(
                    claim_id=claim.id, item_id="test:g1",
                    cluster_id=f"cluster-{i}-a", source_type="v2ex", score=1.0,
                ),
                EvidenceLink(
                    claim_id=claim.id, item_id="test:g2",
                    cluster_id=f"cluster-{i}-b", source_type="rss", score=0.5,
                ),
            ]
        )
        store.set_status(claim.id, "linked")
    store.recompute_independence()
    # deterministic mode: no client -> zero grades
    analyzer = ClaimAnalyzer(store, corpus, client=None)
    assert asyncio.run(analyzer.grade_pending(max_calls=3)) == 0
    # with client: bounded by max_calls
    grade = json.dumps({"verdict": "supported", "confidence": 0.9, "reason": "ok"})
    llm = FakeLLM([grade, grade, grade, grade, grade])
    analyzer2 = ClaimAnalyzer(store, corpus, client=llm)
    graded = asyncio.run(analyzer2.grade_pending(max_calls=3))
    assert graded == 3
    assert analyzer2.llm_calls == 3
    stats = store.stats()
    assert stats["by_status"].get("graded") == 3


def test_grade_claim_parses_and_stores(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    corpus.add_items(
        [make_item("s1", "来源一", "内容说发布已完成", SourceType.RSS),
         make_item("s2", "来源二", "官方公告确认发布时间", SourceType.V2EX)]
    )
    claim = Claim(id="claim:test:s1:0:verified", item_id="test:s1", text="项目已发布")
    store.upsert_claims([claim])
    store.add_evidence(
        [
            EvidenceLink(claim.id, "test:s1", "c1", "rss", 2.0),
            EvidenceLink(claim.id, "test:s2", "c2", "v2ex", 1.0),
        ]
    )
    store.set_status(claim.id, "linked")
    llm = FakeLLM(
        ['```json\n{"verdict": "contested", "confidence": 0.7, "reason": "来源二时间不一致"}\n```']
    )
    analyzer = ClaimAnalyzer(store, corpus, client=llm)
    graded = asyncio.run(analyzer.grade_claim(claim))
    assert graded is not None
    assert graded.verdict == "contested"
    assert 0.0 <= graded.confidence <= 1.0
    row = store.get_claim(claim.id)
    assert row.status == "graded" and row.verdict == "contested"
    # prompt must mark provenance vs independent sources
    prompt = llm.prompts[-1][1]
    assert "origin" in prompt and "independent" in prompt


def test_grade_rejects_invalid_verdict(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    claim = Claim(id="claim:test:bad:0:x", item_id="test:bad", text="某个声明")
    store.upsert_claims([claim])
    store.add_evidence([EvidenceLink(claim.id, "test:bad", None, "v2ex", 1.0)])
    store.set_status(claim.id, "linked")
    llm = FakeLLM(['{"verdict": "probably-true", "confidence": 0.5}'])
    analyzer = ClaimAnalyzer(store, corpus, client=llm)
    assert asyncio.run(analyzer.grade_claim(claim)) is None
    assert store.get_claim(claim.id).status == "linked"  # unchanged
