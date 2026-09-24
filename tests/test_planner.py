"""Tests for the AIClient→Planner adapter (contract handling only).

The research loop's use of a planner is covered in test_research_session;
here we pin the JSON contracts, the repair retry, and evidence rendering
into prompts (claim verdicts must reach the model).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.research.planner import LLMPlanner

NOW = "2026-09-24T00:00:00+00:00"


class Recorder:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[tuple[str, str]] = []

    async def complete(self, system, user, **kw):
        self.seen.append((system, user))
        return self.replies.pop(0)


def evidence():
    return [
        {
            "id": "res:1", "title": "DeepSeek-V4 发布", "url": "https://e.com/1",
            "source_type": "v2ex", "published_at": NOW,
            "snippet": "V4 模型上线，代码能力提升。",
            "claims": [
                {"text": "V4 已发布", "verdict": "supported", "independent_sources": 3},
                {"text": "延迟 30ms", "verdict": None, "independent_sources": 1},
            ],
        },
        {
            "id": "res:2", "title": "另一来源", "url": "https://e.com/2",
            "source_type": "rss", "published_at": NOW, "snippet": "补充信息。",
            "claims": [],
        },
    ]


def test_decompose_parses_list() -> None:
    client = Recorder([json.dumps({"subquestions": ["性能如何", "价格几何"]})])
    subs = asyncio.run(LLMPlanner(client).decompose("V4 怎么样", []))
    assert subs == ["性能如何", "价格几何"]


def test_decompose_repairs_invalid_json_once() -> None:
    client = Recorder(["not json at all", json.dumps({"subquestions": ["a"]})])
    subs = asyncio.run(LLMPlanner(client).decompose("q", []))
    assert subs == ["a"]
    assert len(client.seen) == 2  # one repair retry
    assert "did not satisfy the output contract" in client.seen[1][1]


def test_decompose_raises_after_second_failure() -> None:
    client = Recorder(["junk", "still junk"])
    with pytest.raises(ValueError):
        asyncio.run(LLMPlanner(client).decompose("q", []))


def test_answer_renders_citations_and_verdicts() -> None:
    client = Recorder([json.dumps({"answer": "已发布[1]。"}, ensure_ascii=False)])
    planner = LLMPlanner(client)
    answer = asyncio.run(planner.answer("主问题", "V4 是否发布", evidence()))
    assert answer == "已发布[1]。"
    prompt = client.seen[-1][1]
    assert "[1] (v2ex, 2026-09-24)" in prompt
    assert "[2] (rss," in prompt
    assert "supported(3源)" in prompt  # graded verdict visible to the model
    assert "延迟 30ms" not in prompt  # ungraded claims are noise, dropped


def test_answer_requires_answer_field() -> None:
    # first reply lacks "answer"; repair reply provides it
    client = Recorder(
        [json.dumps({"text": "wrong key"}), json.dumps({"answer": "ok"})]
    )
    assert asyncio.run(LLMPlanner(client).answer("q", "sq", [])) == "ok"


def test_answer_rejects_blank_after_repair() -> None:
    client = Recorder([json.dumps({"answer": "  "}), json.dumps({"answer": ""})])
    with pytest.raises(ValueError):
        asyncio.run(LLMPlanner(client).answer("q", "sq", []))


def test_revise_returns_add_drop() -> None:
    client = Recorder(
        [json.dumps({"add": ["新方向"], "drop": ["旧问题", "  "]}, ensure_ascii=False)]
    )
    delta = asyncio.run(LLMPlanner(client).revise("主", "换个方向", ["旧问题"]))
    assert delta == {"add": ["新方向"], "drop": ["旧问题"]}
    assert "OPEN SUB-QUESTIONS" in client.seen[-1][1]
