from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import DiscourseConfig
from src.scrapers.discourse import DiscourseScraper, _parse_discourse_time


SINCE = datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)
BASE = "https://forum.test"


def _latest() -> dict:
    return {
        "topic_list": {
            "topics": [
                {
                    "id": 7,
                    "fancy_title": "为什么我的构建失败了",
                    "slug": "why-build-fails",
                    "created_at": "2026-09-22T09:00:00.000Z",
                    "posts_count": 9,
                    "views": 250,
                    "like_count": 12,
                    "tags": ["help", "build"],
                    "excerpt": "列表页摘要",
                },
                {  # archived → skip
                    "id": 8,
                    "fancy_title": "归档帖",
                    "created_at": "2026-09-22T09:00:00.000Z",
                    "archived": True,
                },
                {  # older than SINCE → skip
                    "id": 9,
                    "fancy_title": "旧帖",
                    "created_at": "2026-09-01T09:00:00.000Z",
                },
            ]
        }
    }


def _thread() -> dict:
    return {
        "post_stream": {
            "posts": [
                {
                    "username": "asker",
                    "cooked": "<p>编译器报 <code>error[E0502]</code></p>",
                },
                {
                    "username": "helper",
                    "cooked": "<p>借用冲突，试试 clone</p>",
                },
                {
                    "username": "op",
                    "cooked": "<p>解决了，谢谢</p>",
                },
            ]
        }
    }


def _client(thread: dict | None = None) -> AsyncMock:
    payload = _thread() if thread is None else thread

    async def _get(url, params=None, **kwargs):
        response = MagicMock()
        response.raise_for_status.return_value = None
        if url.endswith("/latest.json"):
            response.json.return_value = _latest()
        elif url.endswith("/t/7.json"):
            response.json.return_value = payload
        else:
            response.raise_for_status.side_effect = RuntimeError("404")
            response.json.return_value = {}
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    return client


def test_build_topic_with_floor_discussion() -> None:
    config = DiscourseConfig(
        enabled=True, sites=[{"base_url": BASE, "name": "test-forum"}], fetch_replies=2
    )
    items = asyncio.run(DiscourseScraper(config, _client()).fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.id == "discourse:test-forum:7"
    assert item.title == "为什么我的构建失败了"
    assert str(item.url) == f"{BASE}/t/7"
    assert item.author == "asker"
    assert item.metadata["tags"] == ["help", "build"]
    # HTML stripped to plain text, replies appended
    assert "error[E0502]" in item.content
    assert "<p>" not in item.content
    assert [s.tier for s in item.sections] == ["primary", "community", "community"]
    assert "- @helper: 借用冲突，试试 clone" in item.content
    assert "- @op: 解决了，谢谢" in item.content


def test_thread_fetch_failure_falls_back_to_excerpt() -> None:
    config = DiscourseConfig(enabled=True, sites=[{"base_url": BASE}])

    async def _get(url, params=None, **kwargs):
        response = MagicMock()
        if url.endswith("/latest.json"):
            response.raise_for_status.return_value = None
            response.json.return_value = _latest()
        else:
            response.raise_for_status.side_effect = RuntimeError("boom")
            response.json.return_value = {}
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    items = asyncio.run(DiscourseScraper(config, client).fetch(SINCE))
    assert len(items) == 1
    assert "列表页摘要" in items[0].content


def test_tag_filter() -> None:
    config = DiscourseConfig(
        enabled=True,
        sites=[{"base_url": BASE, "name": "f", "tags": ["nonexistent"]}],
    )
    assert asyncio.run(DiscourseScraper(config, _client()).fetch(SINCE)) == []


def test_time_parser() -> None:
    assert _parse_discourse_time("2026-09-22T09:00:00.000Z") == datetime(
        2026, 9, 22, 9, 0, tzinfo=timezone.utc
    )
    assert _parse_discourse_time("not-a-date") is None
    assert _parse_discourse_time("") is None
