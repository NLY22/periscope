from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import V2EXConfig
from src.scrapers.v2ex import V2EXScraper


SINCE = datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)
FRESH = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc).timestamp()
STALE = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc).timestamp()


def _topics() -> list:
    return [
        {
            "id": 100,
            "title": "新帖子",
            "url": "https://www.v2ex.com/t/100",
            "content": "正文内容",
            "created": FRESH,
            "replies": 42,
            "member": {"username": "alice"},
            "node": {"title": "程序员", "slug": "programmer"},
            "last_reply_by": "bob",
        },
        {
            "id": 101,
            "title": "旧帖子",
            "url": "https://www.v2ex.com/t/101",
            "created": STALE,
            "replies": 99,
            "member": {"username": "carol"},
        },
    ]


def _replies() -> list:
    return [
        {"content": "第一条   回复", "member": {"username": "dave"}},
        {"content": "", "member": {"username": "erin"}},
        {"content": "第三条回复", "member": {"username": "frank"}},
    ]


def _client() -> AsyncMock:
    async def _get(url, params=None, **kwargs):
        response = MagicMock()
        response.raise_for_status.return_value = None
        if "hot" in url:
            response.json.return_value = _topics()
        elif "replies" in url:
            response.json.return_value = _replies()
        else:  # show.json node listing
            response.json.return_value = []
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    return client


def test_fetch_hot_topics_with_replies() -> None:
    scraper = V2EXScraper(V2EXConfig(enabled=True), _client())
    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1  # stale topic dropped
    item = items[0]
    assert item.id == "v2ex:topic:100"
    assert item.title == "新帖子"
    assert item.author == "alice"
    assert item.metadata["node"] == "程序员"
    assert item.metadata["replies"] == 42
    assert "正文内容" in item.content
    assert "- @dave: 第一条 回复" in item.content  # whitespace collapsed
    assert "- @frank: 第三条回复" in item.content
    assert "erin" not in item.content  # empty reply dropped


def test_node_dedup_across_hot_and_nodes() -> None:
    seen: list = []

    async def _get(url, params=None, **kwargs):
        response = MagicMock()
        response.raise_for_status.return_value = None
        if "hot" in url:
            response.json.return_value = _topics()
        elif params and params.get("node_name") == "programmer":
            response.json.return_value = [_topics()[0]]  # duplicate of hot topic
            seen.append("node")
        else:
            response.json.return_value = []
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    scraper = V2EXScraper(
        V2EXConfig(enabled=True, nodes=["programmer"]), client
    )
    items = asyncio.run(scraper.fetch(SINCE))
    assert len(items) == 1
    assert seen == ["node"]


def test_disabled_returns_empty() -> None:
    scraper = V2EXScraper(V2EXConfig(enabled=False), _client())
    assert asyncio.run(scraper.fetch(SINCE)) == []


def test_api_failure_is_swallowed() -> None:
    client = AsyncMock()
    client.get.side_effect = RuntimeError("boom")
    scraper = V2EXScraper(V2EXConfig(enabled=True), client)
    assert asyncio.run(scraper.fetch(SINCE)) == []
