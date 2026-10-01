"""Orchestrator wiring for the correctness loop (Phase C).

Focus: the pipeline must keep working when no LLM is configured — the
corpus accumulates, claims that exist still get linked, and nothing
raises. Full analyzer semantics live in test_claims.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from rich.console import Console

from src.analysis import Claim, ClaimStore
from src.models import AnalysisConfig, ContentItem, CorpusConfig, SourceType
from src.orchestrator import HorizonOrchestrator, _AI_UNSET

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def make_orchestrator(tmp_path, monkeypatch) -> HorizonOrchestrator:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    orch = HorizonOrchestrator.__new__(HorizonOrchestrator)
    orch.config = SimpleNamespace(
        corpus=CorpusConfig(),
        analysis=AnalysisConfig(),
        ai=SimpleNamespace(
            provider="openai", model="gpt-test", api_key_env="DEFINITELY_UNSET_KEY"
        ),
    )
    orch.storage = SimpleNamespace(data_dir=str(tmp_path))
    orch.console = Console(record=True, quiet=True)
    orch.icons = {"ai": "*", "fetched": "#"}
    orch._corpus = None
    orch._ai_client_cache = _AI_UNSET
    return orch


def make_item(idx: str, title: str, content: str) -> ContentItem:
    return ContentItem(
        id=f"wire:{idx}",
        source_type=SourceType.V2EX,
        title=title,
        url=f"https://example.com/{idx}",
        content=content,
        published_at=NOW,
        fetched_at=NOW,
    )


def test_analyze_claims_without_llm_is_deterministic(tmp_path, monkeypatch) -> None:
    orch = make_orchestrator(tmp_path, monkeypatch)
    items = [
        make_item("1", "DeepSeek-V4 发布", "DeepSeek 于 9 月发布 V4 模型，主打代码能力。"),
        make_item("2", "发布说明", "DeepSeek-V4 模型 9 月上线，支持百万上下文。"),
    ]
    orch.persist_to_corpus(items, NOW)
    corpus = orch._get_corpus()
    store = ClaimStore(corpus)
    # simulate an already-extracted claim (extraction needs the LLM)
    store.upsert_claims(
        [Claim(id="claim:wire:1:0:v4", item_id="wire:1", text="DeepSeek 于 9 月发布 V4 模型")]
    )

    asyncio.run(orch.analyze_claims(items))  # must not raise

    claim = store.get_claim("claim:wire:1:0:v4")
    assert claim.status == "linked"  # linking still ran
    assert orch._ai_client_cache is None  # LLM absence cached, not retried


def test_analyze_claims_disabled_by_config(tmp_path, monkeypatch) -> None:
    orch = make_orchestrator(tmp_path, monkeypatch)
    orch.config.analysis.enabled = False
    corpus = orch._get_corpus()
    assert corpus is not None  # corpus itself stays on
    asyncio.run(orch.analyze_claims([make_item("z", "t", "content")]))
    assert corpus.stats()["items"] == 0  # nothing else happened either


def test_persist_then_analyze_keeps_pipeline_survivable(tmp_path, monkeypatch) -> None:
    """Corpus corruption must degrade both stages, never crash run()."""
    orch = make_orchestrator(tmp_path, monkeypatch)
    # point the corpus path at something unusable
    orch.config.corpus.path = str(tmp_path.parent) + "/..//dev/null-impossible/x.db"
    items = [make_item("e", "err", "body")]
    orch.persist_to_corpus(items, NOW)  # swallowed
    asyncio.run(orch.analyze_claims(items))  # swallowed as well


def test_analyze_claims_skips_items_the_corpus_has_already_seen(tmp_path, monkeypatch):
    """A re-fetched item must not buy a second extraction pass.

    An item whose source has no publish time has no `since` filter, so it
    comes back every run. INSERT OR IGNORE stops the duplicate row, not the
    duplicate spend.
    """
    orch = make_orchestrator(tmp_path, monkeypatch)
    calls = []

    class RecordingAnalyzer:
        llm_calls = 0

        class store:
            @staticmethod
            def stats():
                return {"claims": 0, "multi_source": 0}

        async def extract_claims(self, item):
            calls.append(item.id)
            return []

        def link_all_pending(self):
            return 0

        async def grade_pending(self, max_calls):
            return 0

    monkeypatch.setattr(
        HorizonOrchestrator, "_get_claim_analyzer", lambda self: RecordingAnalyzer()
    )

    def timeless(idx):
        return ContentItem(
            id=f"seen:{idx}", source_type=SourceType.V2EX, title=f"t{idx}",
            url=f"https://e.com/{idx}", content=f"body {idx}",
            published_at=None, fetched_at=NOW,
        )

    first, second = timeless("1"), timeless("2")
    assert first.time_basis == "unknown"

    orch.persist_to_corpus([first, second], NOW)
    asyncio.run(orch.analyze_claims([first, second]))
    assert sorted(calls) == ["seen:1", "seen:2"]

    calls.clear()
    orch.persist_to_corpus([first, second], NOW)      # nothing new is stored
    asyncio.run(orch.analyze_claims([first, second]))
    assert calls == []

    calls.clear()
    third = timeless("3")
    orch.persist_to_corpus([first, third], NOW)       # only `third` is new
    asyncio.run(orch.analyze_claims([first, third]))
    assert calls == ["seen:3"]

    orch._get_corpus().close()


def test_linking_and_grading_still_run_when_nothing_is_new(tmp_path, monkeypatch):
    """Filtering extraction must not skip the free, resumable stages."""
    orch = make_orchestrator(tmp_path, monkeypatch)
    ran = []

    class RecordingAnalyzer:
        llm_calls = 0

        class store:
            @staticmethod
            def stats():
                return {"claims": 0, "multi_source": 0}

        async def extract_claims(self, item):
            ran.append(("extract", item.id))
            return []

        def link_all_pending(self):
            ran.append(("link",))
            return 0

        async def grade_pending(self, max_calls):
            ran.append(("grade",))
            return 0

    monkeypatch.setattr(
        HorizonOrchestrator, "_get_claim_analyzer", lambda self: RecordingAnalyzer()
    )

    asyncio.run(orch.analyze_claims([make_item("ghost", "t", "b")]))
    assert ("link",) in ran
    assert ("grade",) in ran
    assert not [r for r in ran if r[0] == "extract"]
    orch._get_corpus().close()
