"""Tests for the long-session research orchestrator (Phase D).

The planner seam is always faked; the value under test is the state
machine: persistence, checkpoints, budgets, honest partial reports, and
follow-up tree revision.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.corpus.store import Corpus
from src.models import ContentItem, SourceType
from src.research import ResearchSession, ResearchStore

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


class FakePlanner:
    def __init__(self, subs=None, answers=None, delta=None, fail=()):
        self.subs = subs or []
        self.answers = answers or {}
        self.delta = delta or {}
        self.fail = set(fail)
        self.calls: list[str] = []

    async def decompose(self, question, prior):
        self.calls.append("decompose")
        if "decompose" in self.fail:
            raise RuntimeError("llm down")
        return list(self.subs)

    async def answer(self, question, subquestion, evidence):
        self.calls.append(f"answer:{subquestion}")
        if "answer" in self.fail:
            raise RuntimeError("rate limited")
        template = self.answers.get(subquestion, "根据证据，此问题结论明确。")
        return template.format(n=len(evidence)) if evidence else template

    async def revise(self, question, user_message, open_subquestions):
        self.calls.append("revise")
        return dict(self.delta)


def item(idx: str, title: str, content: str) -> ContentItem:
    return ContentItem(
        id=f"res:{idx}", source_type=SourceType.V2EX, title=title,
        url=f"https://example.com/{idx}", content=content,
        published_at=NOW, fetched_at=NOW,
    )


@pytest.fixture()
def env(tmp_path):
    corpus = Corpus(tmp_path / "r.db")
    corpus.add_items(
        [
            item("1", "DeepSeek-V4 发布", "DeepSeek 发布 V4 模型，代码能力提升明显。"),
            item("2", "行业观察", "多位工程师实测 DeepSeek-V4，延迟约 30ms。"),
            item("3", "无关", "今天天气不错适合跑步。"),
        ]
    )
    store = ResearchStore(corpus)
    yield corpus, store
    corpus.close()


def session(store, corpus, planner=None, budget=12) -> ResearchSession:
    return ResearchSession(store, corpus, planner=planner, planner_budget_per_invocation=budget)


# ------------------------------------------------------------------ lifecycle
def test_start_decomposes_answers_and_reports(env) -> None:
    corpus, store = env
    planner = FakePlanner(
        subs=["DeepSeek-V4 的性能如何", "发布时间是什么时候"],
        answers={"DeepSeek-V4 的性能如何": "共 {n} 条证据支持性能提升。"},
    )
    rs = session(store, corpus, planner)
    report = asyncio.run(rs.start("DeepSeek-V4 到底怎么样"))
    assert report.session_id
    assert "研究报告：DeepSeek-V4 到底怎么样" in report.markdown
    assert "共 2 条证据支持性能提升" in report.markdown
    # second sub-question answered by generic text
    assert report.markdown.count("已回答") >= 1
    sess = store.get_session(report.session_id)
    assert sess.status == "reported"
    assert len(store.subquestions(sess.id)) == 2
    # persisted report turn
    assert [t.role for t in store.turns(sess.id)] == ["user", "report"]


def test_report_has_numbered_citations(env) -> None:
    corpus, store = env
    planner = FakePlanner(subs=["DeepSeek-V4 的性能如何"])
    rs = session(store, corpus, planner)
    report = asyncio.run(rs.start("Q"))
    assert "## 引用" in report.markdown
    assert "[1]" in report.markdown and "[2]" in report.markdown
    assert "example.com/1" in report.markdown


def test_no_planner_yields_honest_unanswered_report(env) -> None:
    corpus, store = env
    rs = session(store, corpus, planner=None)
    report = asyncio.run(rs.start("无人回答的问题"))
    assert "尚未回答" in report.markdown
    assert "0/1 个子问题已回答" in report.markdown
    # state machine still consistent: sub-question persists as open
    sqs = store.subquestions(report.session_id)
    assert sqs[0].status == "open"


def test_unanswered_subquestion_still_gathers_and_shows_evidence(env) -> None:
    """Degraded mode is not dead mode: evidence accumulates as a checkpoint."""
    corpus, store = env
    rs = session(store, corpus, planner=None)
    report = asyncio.run(rs.start("DeepSeek-V4 的性能如何"))
    sqs = store.subquestions(report.session_id)
    assert sqs[0].status == "open"
    assert len(sqs[0].evidence_ids) >= 2  # matched the two DeepSeek items
    assert "已收集证据，待分析" in report.markdown
    assert "## 引用" in report.markdown


def test_budget_bounds_planner_answer_calls(env) -> None:
    corpus, store = env
    planner = FakePlanner(
        subs=[f"子问题 {i} DeepSeek" for i in range(5)],
        answers={},
    )
    # decompose 1 call + budget 2 -> only 2 answers
    rs = session(store, corpus, planner, budget=3)
    report = asyncio.run(rs.start("预算测试"))
    answers = [c for c in planner.calls if c.startswith("answer:")]
    assert len(answers) == 2
    assert "3/5" not in report.markdown  # partially answered is stated honestly


def test_decompose_failure_falls_back_to_raw_question(env) -> None:
    corpus, store = env
    planner = FakePlanner(fail={"decompose"})
    rs = session(store, corpus, planner)
    report = asyncio.run(rs.start("降级也要能跑"))
    sqs = store.subquestions(report.session_id)
    assert len(sqs) == 1 and sqs[0].text == "降级也要能跑"


# ------------------------------------------------------------------ follow-up
def test_followup_revises_tree_and_reinvestigates(env) -> None:
    corpus, store = env
    planner = FakePlanner(
        subs=["DeepSeek-V4 的性能如何", "价格贵不贵"],
        delta={"add": ["竞品对比呢"], "drop": ["价格贵不贵"]},
    )
    rs = session(store, corpus, planner)
    first = asyncio.run(rs.start("DeepSeek-V4 怎么样"))
    assert "价格贵不贵" in first.markdown
    second = asyncio.run(rs.followup(first.session_id, "别管价格了，看看竞品对比"))
    assert "竞品对比呢" in second.markdown
    assert "价格贵不贵" not in second.markdown  # dropped sub-questions vanish
    texts = {s.text: s.status for s in store.subquestions(first.session_id)}
    assert texts["价格贵不贵"] == "dropped"
    assert "竞品对比呢" in texts
    turns = [t.role for t in store.turns(first.session_id)]
    assert turns == ["user", "report", "user", "report"]


def test_followup_unknown_session_raises(env) -> None:
    corpus, store = env
    rs = session(store, corpus, FakePlanner())
    with pytest.raises(KeyError):
        asyncio.run(rs.followup("ses_nope", "hi"))


def test_followup_without_planner_still_reinvestigates(env) -> None:
    corpus, store = env
    rs = session(store, corpus, planner=None)
    first = asyncio.run(rs.start("无人研究"))
    second = asyncio.run(rs.followup(first.session_id, "继续"))
    assert "尚未回答" in second.markdown
    assert len(store.turns(first.session_id)) == 4


# ---------------------------------------------------------------- persistence
def test_state_survives_reopen(env, tmp_path) -> None:
    corpus, store = env
    planner = FakePlanner(subs=["DeepSeek-V4 的性能如何"])
    rs = session(store, corpus, planner)
    report = asyncio.run(rs.start("重启测试"))
    corpus.close()
    # reopen: same file, same session, same answers
    corpus2 = Corpus(tmp_path / "r.db")
    store2 = ResearchStore(corpus2)
    rs2 = session(store2, corpus2, planner=None)
    sqs = store2.subquestions(report.session_id)
    assert sqs and sqs[0].status == "answered"
    reopened = rs2.render_report(report.session_id)
    assert "DeepSeek-V4 的性能如何" in reopened
    corpus2.close()


def test_stats_shape(env) -> None:
    corpus, store = env
    asyncio.run(session(store, corpus, FakePlanner(subs=["a"])).start("统计"))
    stats = store.stats()
    assert stats["sessions"] == 1
    assert stats["turns"] == 2
