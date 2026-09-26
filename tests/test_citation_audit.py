"""Citation audit: a finished report must be checkable from the outside.

The report renderer already cannot invent a reference (it looks the item up
before numbering it). These tests pin the stronger claim — every numbered
reference resolves to a stored item *with author-written text behind it* — and
that the audit footer is emitted on real reports.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.corpus.citations import audit_report
from src.corpus.store import Corpus
from src.models import ContentItem, SourceType
from src.research.session import ResearchSession, ResearchStore

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def item(idx: str, source: SourceType, title: str, content: str) -> ContentItem:
    return ContentItem(
        id=f"cit:{idx}",
        source_type=source,
        title=title,
        url=f"https://cite.example.com/{idx}",
        content=content,
        author="tester",
        published_at=NOW,
        fetched_at=NOW,
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / "cite.db")
    c.add_items(
        [
            item("real", SourceType.RSS, "真实报道", "OpenForge 完成 B 轮融资，估值 30 亿。"),
            item("crowd", SourceType.V2EX, "只有回帖", "【回复精选】\n有人称 OpenForge 估值 30 亿。"),
            item("uncited", SourceType.HACKERNEWS, "未被引用的条目", "Another story entirely."),
        ]
    )
    return c


REPORT = """# 研究报告：OpenForge 到底融到钱没有

正文引用 [1] 与 [2]，另有 [9]。

## 引用
[1] `rss` 真实报道 — https://cite.example.com/real
[2] `v2ex` 只有回帖 — https://cite.example.com/crowd
[3] `hackernews` 未被引用的条目 — https://cite.example.com/uncited
"""


def test_audit_flags_ghosts_crowd_only_and_dangling(corpus: Corpus) -> None:
    audit = audit_report(REPORT, corpus)
    # [2] does resolve to a stored item — the violation is that the item has
    # nothing but crowd text behind it, which is a separate finding.
    assert audit.resolved == [1, 2]
    assert audit.crowd_only == [2]        # cited item has no author-written text
    assert audit.ghosts == [9]            # inline marker with no reference line
    assert audit.dangling == [3]          # listed but never cited
    assert audit.ok is False
    assert "仅人群发言支撑" in audit.summary()


def test_clean_report_passes(corpus: Corpus) -> None:
    clean = (
        "正文引用 [1]。\n\n## 引用\n"
        "[1] `rss` 真实报道 — https://cite.example.com/real\n"
    )
    audit = audit_report(clean, corpus)
    assert audit.ok and not audit.dangling
    assert audit.summary().endswith("每条引用都指向有作者亲写文本的存储条目。")


def test_report_carries_the_audit_footer(corpus: Corpus) -> None:
    session = ResearchSession(
        store=ResearchStore(corpus), corpus=corpus, planner=None, evidence_per_question=3
    )
    report = asyncio.run(session.start("OpenForge 融资 估值")).markdown
    assert "## 引用" in report
    assert "> 引用核验：" in report
    # the footer must reflect reality, not just exist
    audit = audit_report(report, corpus)
    assert audit.missing_items == []
