"""Report templates: 背景调查 / 市场调研 / 方法探索.

Section assignment is heuristic on purpose, but the three computed blocks are
built only from stored data: dates for the timeline, contested verdicts for the
disagreements, unresolved sub-questions for the open list. An empty section
must read as 未覆盖, never as padded prose.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.corpus.store import Corpus
from src.models import ContentItem, SourceType
from src.research.session import ResearchSession, ResearchStore
from src.research.templates import (
    BACKGROUND,
    MARKET,
    build_skeleton,
    resolve_template,
)

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def item(idx: str, title: str, content: str, days: int) -> ContentItem:
    from datetime import timedelta

    stamp = NOW - timedelta(days=days)
    return ContentItem(
        id=f"tpl:{idx}",
        source_type=SourceType.RSS,
        title=title,
        url=f"https://tpl.example.com/{idx}",
        content=content,
        author="tester",
        published_at=stamp,
        fetched_at=NOW,
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / "tpl.db")
    c.add_items(
        [
            item("a", "行业规模报告", "中国人形机器人市场规模 增长 到 2028 年翻三倍。", 1),
            item("b", "厂商动态", "三家厂商发布 新机型。", 2),
        ]
    )
    return c


# ------------------------------------------------------------------- resolve
def test_explicit_name_and_disable_paths() -> None:
    assert resolve_template("market") is MARKET
    assert resolve_template("background") is BACKGROUND
    assert resolve_template("flat", "随便什么都行") is None
    assert resolve_template("none") is None
    assert resolve_template("nonsense-name") is None


def test_auto_infers_only_when_the_question_asks_for_a_shape() -> None:
    assert resolve_template("auto", "中国人形机器人 市场规模 与 增长 玩家").name == "market"
    assert resolve_template("auto", "这起事故 发生了什么 经过 为什么").name == "background"
    assert resolve_template("auto", "DeepSeek-V4 到底怎么样") is None
    assert resolve_template("auto", "Q") is None


# ------------------------------------------------------------------- sections
def test_section_assignment_prefers_the_best_keyword_match() -> None:
    assert MARKET.section_for("市场规模与增长趋势") == "规模与增长"
    assert MARKET.section_for("主要玩家有哪些") == "主要玩家"
    assert BACKGROUND.section_for("各方说法是什么，谁声称的") == "参与方与说法"
    assert BACKGROUND.section_for("今天天气不错") is None


def test_decompose_guidance_lists_every_section() -> None:
    guidance = MARKET.decompose_guidance()
    assert "规模与增长" in guidance and "风险与不确定性" in guidance


# ------------------------------------------------------------------- skeleton
def test_skeleton_groups_and_computes_blocks_from_stored_rows(corpus: Corpus) -> None:
    class SQ:
        def __init__(self, sq_id, text, status, answer=None, evidence_ids=None):
            self.id, self.text, self.status, self.answer = sq_id, text, status, answer
            self.evidence_ids = evidence_ids or []

    rows = [
        {"id": "tpl:a", "title": "行业规模报告", "url": "u", "published_at": "2026-09-19T00:00:00+00:00",
         "claims": [{"verdict": "contested", "text": "规模翻倍"}]},
        {"id": "tpl:b", "title": "厂商动态", "url": "v", "published_at": "2026-09-21T00:00:00+00:00",
         "claims": []},
    ]
    subs = [SQ("s1", "市场规模多大", "answered", "大约三倍", ["tpl:a"]),
            SQ("s2", "监管风险呢", "open", None, ["tpl:b"])]

    skeleton = build_skeleton(MARKET, subs, rows)
    assert skeleton["sections"]["规模与增长"] == [subs[0]]
    assert skeleton["open_questions"] == [subs[1]]
    assert [entry["date"] for entry in skeleton["timeline"]] == ["2026-09-19", "2026-09-21"]
    assert skeleton["disagreements"] == [rows[0]]


# ------------------------------------------------------------------- rendering
def test_render_uses_the_template_and_marks_empty_sections(corpus: Corpus) -> None:
    class Planner:
        async def decompose(self, question, prior):
            return ["市场规模多大", "主要玩家是谁"]

        async def answer(self, question, subquestion, evidence):
            return f"{subquestion} 的回答 [1]"

        async def revise(self, question, user_message, open_subquestions):
            return {"add": [], "drop": []}

    session = ResearchSession(
        store=ResearchStore(corpus),
        corpus=corpus,
        planner=Planner(),
        report_template="market",
        evidence_per_question=2,
    )
    report = asyncio.run(session.start("中国人形机器人 市场规模 与 增长")).markdown

    assert "报告骨架：市场调研" in report
    assert "## 规模与增长" in report and "### 市场规模多大" in report
    assert "## 时间线" in report and "## 分歧点" in report and "## 未决问题" in report
    # sections the two sub-questions never reached stay visible as gaps
    assert "未覆盖" in report
    assert "（核查层尚未发现相互矛盾的独立说法）" in report


def test_flat_template_renders_as_before(corpus: Corpus) -> None:
    session = ResearchSession(
        store=ResearchStore(corpus), corpus=corpus, planner=None, report_template="flat"
    )
    report = asyncio.run(session.start("中国人形机器人 市场规模 与 增长")).markdown
    assert "报告骨架" not in report
    assert "## 时间线" not in report
    assert "## 规模与增长" not in report
