from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import BilibiliConfig
from src.scrapers.bilibili import BilibiliScraper


SINCE = datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)
PUBDATE = datetime(2026, 9, 22, 8, 0, 0, tzinfo=timezone.utc).timestamp()


def _popular_payload() -> dict:
    return {
        "code": 0,
        "data": {
            "list": [
                {
                    "reason_type": 1,  # banner slot, must be skipped
                    "bvid": "BVbanner",
                    "title": "banner",
                    "aid": 1,
                    "stat": {"view": 999999},
                },
                {
                    "bvid": "BV1test",
                    "aid": 222,
                    "title": "  B站测试视频  ",
                    "desc": "简介\n\n多行   空白",
                    "pubdate": PUBDATE,
                    "duration": 300,
                    "tname": "科技",
                    "owner": {"name": "UP主", "mid": 9},
                    "stat": {
                        "view": 5000,
                        "like": 100,
                        "coin": 40,
                        "favorite": 30,
                        "danmaku": 12,
                        "reply": 5,
                    },
                },
                {
                    "bvid": "BVtooold",
                    "aid": 333,
                    "title": "旧视频",
                    "pubdate": datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp(),
                    "owner": {"name": "老UP", "mid": 8},
                    "stat": {"view": 8000},
                },
            ]
        },
    }


def _comments_payload() -> dict:
    return {
        "code": 0,
        "data": {
            "replies": [
                {"content": {"message": "第一条热评"}, "member": {"uname": "甲"}},
                {"content": {"message": "   "}, "member": {"uname": "乙"}},
                {"content": {"message": "补充观点"}, "member": {"uname": "丙"}},
            ]
        },
    }


def _client() -> AsyncMock:
    async def _get(url, params=None, **kwargs):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = (
            _comments_payload() if "reply" in url else _popular_payload()
        )
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    return client


def test_fetch_skips_banner_and_stale_and_builds_item() -> None:
    scraper = BilibiliScraper(BilibiliConfig(enabled=True), _client())
    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.id == "bilibili:video:BV1test"
    assert item.title == "B站测试视频"
    assert str(item.url) == "https://www.bilibili.com/video/BV1test"
    assert item.author == "UP主"
    assert item.metadata["views"] == 5000
    assert item.metadata["aid"] == 222
    # desc whitespace collapsed, comments appended
    assert item.content.startswith("简介 多行 空白")
    assert "【评论区 Top】" in item.content
    assert "- @甲: 第一条热评" in item.content
    assert "- @丙: 补充观点" in item.content
    assert "@乙" not in item.content  # blank comment dropped


def test_disabled_and_api_error_yield_empty() -> None:
    off = BilibiliScraper(BilibiliConfig(enabled=False), _client())
    assert asyncio.run(off.fetch(SINCE)) == []

    bad = AsyncMock()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"code": -352, "message": "risk blocked"}
    bad.get.return_value = response
    blocked = BilibiliScraper(BilibiliConfig(enabled=True), bad)
    assert asyncio.run(blocked.fetch(SINCE)) == []


def test_comments_disabled() -> None:
    scraper = BilibiliScraper(
        BilibiliConfig(enabled=True, fetch_comments=0), _client()
    )
    items = asyncio.run(scraper.fetch(SINCE))
    assert "评论区" not in (items[0].content or "")
