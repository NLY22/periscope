"""The research loop widens its search instead of accepting the first miss.

These tests drive the ladder with a scripted retriever so the *policy* — what
is tried, in what order, how it is bounded and how it is recorded — is pinned
independently of retrieval quality, which is measured in scripts/eval_retrieval.py.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.corpus.store import Corpus
from src.models import ContentItem, SourceType
from src.research.session import ResearchSession, ResearchStore

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def make_item(idx: str, source: SourceType, title: str) -> ContentItem:
    return ContentItem(
        id=f"wid:{idx}",
        source_type=source,
        title=title,
        url=f"https://example.com/{idx}",
        content=title + " body text",
        author="tester",
        published_at=NOW,
        fetched_at=NOW,
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / "corpus.db")
    c.add_items(
        [
            make_item("news1", SourceType.RSS, "first news"),
            make_item("news2", SourceType.RSS, "second news"),
            make_item("forum1", SourceType.V2EX, "forum thread"),
        ]
    )
    return c


class ScriptedRetriever:
    """Returns corpus rows according to (term_budget, source_types)."""

    def __init__(self, corpus: Corpus, script: Dict[Any, List[str]]):
        self.corpus = corpus
        self.script = script
        self.calls: List[Dict[str, Any]] = []

    async def index_pending(self, batch_size: int = 16) -> int:
        return 0

    async def gather(
        self,
        query: str,
        limit: int,
        term_budget: int = 4,
        source_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        key = (term_budget, tuple(source_types or ()))
        self.calls.append({"query": query, **{"term_budget": term_budget}, "source_types": source_types})
        ids = self.script.get(key, [])
        rows = []
        for item_id in ids:
            row = self.corpus._conn.execute(
                "SELECT * FROM items WHERE id=?", (item_id,)
            ).fetchone()
            if row is not None:
                rows.append(self.corpus._row_to_dict(row))
        return rows


class FakePlanner:
    def __init__(
        self,
        rewrite: Optional[str] = None,
        decide_raises: bool = False,
        answer_raises: bool = False,
    ):
        self.rewrite = rewrite
        self.decide_raises = decide_raises
        self.answer_raises = answer_raises
        self.decided_with: List[str] = []
        self.answered = 0

    async def decompose(self, question, prior):
        return [question]

    async def answer(self, question, subquestion, evidence):
        self.answered += 1
        if self.answer_raises:
            raise RuntimeError("no budget / model unavailable")
        return f"answer for {len(evidence)} items"

    async def revise(self, question, user_message, open_subquestions):
        return {"add": [], "drop": []}

    async def decide(self, question, subquestion, corpus_families):
        self.decided_with.append(subquestion)
        if self.decide_raises:
            raise RuntimeError("model drifted")
        return {"query": self.rewrite or subquestion}


def build_session(corpus, retriever, planner=None, collector=None, rounds=3, min_ev=3):
    return ResearchSession(
        store=ResearchStore(corpus),
        corpus=corpus,
        planner=planner,
        retriever=retriever,
        max_retrieval_rounds=rounds,
        min_evidence_for_answer=min_ev,
        collector=collector,
    )


def start(session, question="does OpenForge exist?"):
    return asyncio.run(session.start(question))


def recorded(corpus) -> List[Dict[str, Any]]:
    store = ResearchStore(corpus)
    rows = corpus._conn.execute(
        "SELECT round_number, action, query, source_types, new_items, subquestion_id"
        " FROM research_actions ORDER BY round_number"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
def test_ladder_widens_until_the_evidence_quota_is_met(corpus: Corpus) -> None:
    retriever = ScriptedRetriever(
        corpus,
        {
            (4, ()): [],                                    # baseline finds nothing
            (2, ()): ["wid:news1", "wid:news2"],             # looser terms find two
            (3, ("v2ex",)): ["wid:forum1"],                  # another slice finds one
        },
    )
    session = build_session(corpus, retriever, min_ev=3)
    start(session)

    got = recorded(corpus)
    assert [row["action"] for row in got] == ["baseline", "widen_terms", "switch_source_family"]
    assert [row["new_items"] for row in got] == [0, 2, 1]
    assert got[2]["source_types"] == '["v2ex"]'


def test_ladder_stops_early_when_the_first_query_is_enough(corpus: Corpus) -> None:
    retriever = ScriptedRetriever(corpus, {(4, ()): ["wid:news1", "wid:news2", "wid:forum1"]})
    session = build_session(corpus, retriever, min_ev=3)
    start(session)
    assert [row["action"] for row in recorded(corpus)] == ["baseline"]


def test_round_budget_bounds_the_ladder(corpus: Corpus) -> None:
    retriever = ScriptedRetriever(corpus, {(4, ()): [], (2, ()): ["wid:news1"]})
    session = build_session(corpus, retriever, rounds=2, min_ev=5)
    start(session)
    assert [row["action"] for row in recorded(corpus)] == ["baseline", "widen_terms"]


def test_rewrite_query_uses_the_planner_and_its_budget(corpus: Corpus) -> None:
    retriever = ScriptedRetriever(
        corpus, {(4, ()): [], (2, ()): [], (3, ()): ["wid:news1", "wid:news2", "wid:forum1"]}
    )
    planner = FakePlanner(rewrite="OpenForge 三十亿")
    session = build_session(corpus, retriever, planner=planner, min_ev=3, rounds=5)
    start(session)

    got = recorded(corpus)
    assert [row["action"] for row in got] == [
        "baseline",
        "widen_terms",
        "switch_source_family",
        "rewrite_query",
    ]
    assert got[3]["query"] == "OpenForge 三十亿"
    assert planner.decided_with == ["does OpenForge exist?"]


def test_switch_source_family_skips_families_already_seen(corpus: Corpus) -> None:
    retriever = ScriptedRetriever(
        corpus,
        {
            (4, ()): [],
            (2, ()): ["wid:news1", "wid:news2"],
            (3, ("v2ex",)): ["wid:forum1"],
        },
    )
    session = build_session(corpus, retriever, min_ev=5, rounds=4)
    start(session)
    # all three families are used up; the last attempt records nothing found
    switch = [row for row in recorded(corpus) if row["action"] == "switch_source_family"]
    assert switch and json.loads(switch[0]["source_types"]) == ["v2ex"]


def test_planner_failure_degrades_to_open_state_not_a_crash(corpus: Corpus) -> None:
    retriever = ScriptedRetriever(corpus, {(4, ()): [], (2, ()): [], (3, ()): []})
    planner = FakePlanner(decide_raises=True, answer_raises=True)
    session = build_session(corpus, retriever, planner=planner, rounds=99, min_ev=3)
    report = start(session)

    got = [row["action"] for row in recorded(corpus)]
    assert got == ["baseline", "widen_terms", "switch_source_family", "rewrite_query"]
    # the answer never landed, so the report says so and lists what was tried
    assert "尚未回答" in report.markdown
    assert "取证尝试" in report.markdown
    assert "rewrite_query" in report.markdown


def test_collector_is_only_used_when_wired(corpus: Corpus) -> None:
    calls: List[str] = []

    async def collector(question: str) -> int:
        calls.append(question)
        return 2

    retriever = ScriptedRetriever(
        corpus,
        {
            (4, ()): [],
            (2, ()): [],
            (3, ()): ["wid:news1", "wid:news2", "wid:forum1"],
            (3, ("rss", "v2ex")): [],
        },
    )
    session = build_session(
        corpus, retriever, collector=collector, rounds=5, min_ev=3
    )
    start(session)
    assert calls == ["does OpenForge exist?"]
    assert "collect_keywords" in [row["action"] for row in recorded(corpus)]


def test_ladder_without_planner_or_retriever_stays_free(corpus: Corpus) -> None:
    session = ResearchSession(
        store=ResearchStore(corpus), corpus=corpus, planner=None, retriever=None
    )
    assert session._retrieval_ladder() == ["baseline", "widen_terms", "switch_source_family"]
    report = start(session)
    assert "does OpenForge exist?" in report.markdown
