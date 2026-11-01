"""Tests for search enhancement: query cleaning, primary public search with DeepSeek backup, and hydration."""
from __future__ import annotations

import pytest

from periscope.search.api import Result, SearchResponse, search_unified
from periscope.search.query import clean_query, enhance_query, generate_search_variants
from periscope.search.hydrate import hydrate_rows
from periscope.topics.models import TopicRequest
from periscope.topics.service import TopicService


def test_query_cleaning_and_extraction():
    # 测试中文自然语言长句清洗
    raw_cn = "请帮我分析一下2026年人形机器人灵巧手的最新进展怎么样？"
    enhanced = enhance_query(raw_cn)
    assert "2026" in enhanced.clean_query
    assert "灵巧手" in enhanced.clean_query
    assert "请帮我" not in enhanced.clean_query
    assert "怎么样" not in enhanced.clean_query
    assert len(enhanced.variants) >= 2

    # 测试英文口语化长句清洗
    raw_en = "Please help me find the latest news about Rust memory safety in 2026"
    enhanced_en = enhance_query(raw_en)
    assert "Rust" in enhanced_en.clean_query
    assert "memory" in enhanced_en.clean_query
    assert "please" not in enhanced_en.clean_query.lower()


def test_search_unified_defaults_to_public_web_even_with_key(monkeypatch):
    from periscope.search import api

    # 即使配置了 API Key，默认也以公网检索为主力
    monkeypatch.setattr(api, "available", lambda: True)
    dummy_results = [
        Result(title="Public Primary Result", url="https://example.com/public", snippet="Snippet", content="Content")
    ]
    monkeypatch.setattr(
        "periscope.search.public_web.search_public_web",
        lambda *args, **kwargs: SearchResponse(results=dummy_results, elapsed=0.3),
    )

    resp, provider = search_unified("人工智能", count=10)
    assert provider == "public_web"
    assert resp.ok
    assert len(resp.results) == 1
    assert resp.results[0].title == "Public Primary Result"


def test_search_unified_falls_back_to_deepseek_when_public_empty(monkeypatch):
    from periscope.search import api

    # 公网检索返回空，但有 DeepSeek Key -> 自动兜底降级到 DeepSeek
    monkeypatch.setattr(api, "available", lambda: True)
    monkeypatch.setattr(
        "periscope.search.public_web.search_public_web",
        lambda *args, **kwargs: SearchResponse(results=[], elapsed=0.1),
    )
    deepseek_results = [
        Result(title="DeepSeek Backup Result", url="https://example.com/ds", snippet="DS Snippet", content="DS Content")
    ]
    monkeypatch.setattr(
        api,
        "search_topic",
        lambda *args, **kwargs: SearchResponse(results=deepseek_results, elapsed=0.5),
    )

    resp, provider = search_unified("复杂主题", count=10)
    assert provider == "deepseek_backup"
    assert resp.ok
    assert len(resp.results) == 1
    assert resp.results[0].title == "DeepSeek Backup Result"


def test_hydrate_rows_replaces_short_body(monkeypatch):
    test_rows = [
        {"url": "https://example.com/article", "title": "Article", "text": "short", "body": "", "authority": 1}
    ]

    async def fake_fetch(*args, **kwargs):
        return "This is a long extracted article body that exceeds 400 characters easily. " * 10

    monkeypatch.setattr("periscope.search.hydrate._fetch_and_extract_body", fake_fetch)

    hydrated = hydrate_rows(test_rows, max_items=1)
    assert len(hydrated[0]["body"]) > 400
    assert hydrated[0]["authority"] >= 2


def test_topic_service_uses_public_search_as_first_class(monkeypatch):
    fake_web_row = {
        "title": "自主公网搜索文章",
        "url": "https://news.example.com/ai-robot",
        "text": "这是一篇关于人形机器人的深度报道正文，内容非常详实完整。" * 10,
        "body": "这是一篇关于人形机器人的深度报道正文，内容非常详实完整。" * 10,
        "author": "TechNews",
        "date": "2026-10-30",
        "source": "public_web",
        "kind": "media",
        "authority": 2,
        "_origin": "api",
        "search_provider": "public_web",
    }

    service = TopicService(
        api_available=lambda: False,
        api_lookup=lambda topic, count, depth: ([fake_web_row], 0.8, ""),
        platform_lookup=lambda *args, **kwargs: ([], {"elapsed": 0.1, "failed": []}),
        local_lookup=lambda *args: ([], 0),
    )

    result = service.search(TopicRequest(topic="请帮我查一下人形机器人", limit=5))
    assert len(result.api_rows) == 1
    assert result.api_rows[0]["title"] == "自主公网搜索文章"
    assert "公网搜索 1 条" in result.breakdown
