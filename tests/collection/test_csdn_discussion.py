from __future__ import annotations

import asyncio
import httpx

from periscope.collection.platforms import csdn
from periscope.topics import enrichment as topic_enrichment


ARTICLE_URL = "https://blog.csdn.net/sample_author/article/details/12345"


def _comment(comment_id, content, user, parent_id=0, *, sub=None):
    return {
        "info": {
            "commentId": comment_id,
            "articleId": "12345",
            "parentId": parent_id,
            "content": content,
            "userName": user,
            "digg": 2,
            "postTime": "2026-10-24 12:00:00",
        },
        "sub": sub or [],
    }


def _reply(comment_id, content, user, parent_id):
    return {
        "commentId": comment_id,
        "articleId": "12345",
        "parentId": parent_id,
        "content": content,
        "userName": user,
        "digg": 1,
        "postTime": "2026-10-24 12:01:00",
    }


def _html():
    return """<html><h1>Test</h1><div id="content_views">
      <p>Full <b>article</b> body.</p><pre>print('ok')</pre>
    </div></html>"""


def _run(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return asyncio.run(csdn.fetch_discussion(ARTICLE_URL, client=client, **kwargs))
    finally:
        asyncio.run(client.aclose())


def test_fetches_article_body_comment_pages_and_nested_replies():
    first = {
        "code": 200,
        "data": {"count": 2, "pageCount": 2, "list": [
            _comment(11, "Top level", "Alice", sub=[
                _reply(12, "Nested response", "Bob", 11)])]},
    }
    second = {
        "code": 200,
        "data": {"count": 2, "pageCount": 2, "list": [
            _comment(21, "Second comment", "Carol")]},
    }

    def handler(request):
        if request.url.path.endswith("/article/details/12345"):
            return httpx.Response(200, text=_html())
        page = int(request.url.params.get("page", "1"))
        payload = first if page == 1 else second
        return httpx.Response(200, json=payload)

    detail, comments, coverage = _run(handler, budget_seconds=5)

    assert "Full article body." in " ".join(detail["text"].split())
    assert "print('ok')" in detail["text"]
    assert [item["text"] for item in comments] == ["Top level", "Second comment"]
    assert comments[0]["replies"][0]["text"] == "Nested response"
    assert comments[0]["replies"][0]["parent_id"] == "11"
    assert coverage["status"] == "complete"
    assert coverage["fetched_roots"] == 2
    assert coverage["fetched_replies"] == 1
    assert coverage["fetched_comments"] == 3
    assert coverage["reported_comments"] == 2
    assert coverage["pages_attempted"] == 2


def test_keeps_article_when_comment_api_fails_and_reports_error():
    def handler(request):
        if request.url.path.endswith("/article/details/12345"):
            return httpx.Response(200, text=_html())
        return httpx.Response(503, text="unavailable")

    detail, comments, coverage = _run(handler, budget_seconds=5)

    assert "Full article body." in " ".join(detail["text"].split())
    assert comments == []
    assert coverage["status"] == "partial"
    assert coverage["errors"] == ["comments:http_503"]


def test_short_page_with_larger_reported_count_is_partial():
    payload = {"code": 200, "data": {"count": 15,
        "list": [_comment(11, "one", "A"), _comment(12, "two", "B")]}}

    def handler(request):
        if request.url.path.endswith("/article/details/12345"):
            return httpx.Response(200, text=_html())
        return httpx.Response(200, json=payload)

    _, comments, coverage = _run(handler, budget_seconds=5, max_comments=10)

    assert len(comments) == 2
    assert coverage["reported_comments"] == 15
    assert coverage["status"] == "partial"
    assert "comment_count_mismatch" in coverage["truncated_by"]


def test_reply_depth_limit_is_reported_in_coverage():
    root = _comment(1, "level 0", "A")
    current = root
    for depth in range(1, 7):
        child = _reply(depth + 1, "level %d" % depth, "A", depth)
        current["sub"] = [child]
        current = child
    payload = {"code": 200, "data": {"count": 1, "pageCount": 1, "list": [root]}}

    def handler(request):
        if request.url.path.endswith("/article/details/12345"):
            return httpx.Response(200, text=_html())
        return httpx.Response(200, json=payload)

    _, comments, coverage = _run(handler, budget_seconds=5)

    level_four = comments[0]["replies"][0]["replies"][0]["replies"][0]["replies"][0]
    assert level_four["id"] == "5"
    assert "replies" not in level_four
    assert coverage["fetched_comments"] == 5
    assert coverage["status"] == "partial"
    assert "reply_depth_limit" in coverage["truncated_by"]


def test_zero_budget_skips_network_and_preserves_partial_coverage():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=_html())

    detail, comments, coverage = _run(handler, budget_seconds=0)

    assert calls == []
    assert detail == {}
    assert comments == []
    assert coverage["status"] == "partial"
    assert coverage["truncated_by"] == ["time_budget"]


def test_timeout_on_later_page_keeps_already_fetched_comments():
    first_page = {"code": 200, "data": {"count": 2, "pageCount": 2,
        "list": [_comment(11, "Captured before timeout", "A")]}}

    async def handler(request):
        if request.url.path.endswith("/article/details/12345"):
            return httpx.Response(200, text=_html())
        if request.url.params.get("page") == "2":
            await asyncio.sleep(0.2)
        return httpx.Response(200, json=first_page)

    detail, comments, coverage = _run(handler, budget_seconds=0.1)

    assert "Full article body." in " ".join(detail["text"].split())
    assert comments[0]["text"] == "Captured before timeout"
    assert coverage["status"] == "partial"
    assert "time_budget" in coverage["truncated_by"]


def test_invalid_or_non_csdn_article_urls_do_not_make_requests():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=_html())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        detail, comments, coverage = asyncio.run(csdn.fetch_discussion(
            "https://example.com/article/details/12345", client=client))
    finally:
        asyncio.run(client.aclose())

    assert calls == []
    assert detail == {}
    assert comments == []
    assert coverage["status"] == "failed"
    assert coverage["errors"] == ["invalid_article_url"]


def test_topic_enrichment_attaches_csdn_materials_and_passes_remaining_budget(monkeypatch):
    received = {}

    async def fake_fetch(url, *, budget_seconds):
        received["url"] = url
        received["budget"] = budget_seconds
        return ({"article_id": "12345", "text": "Full body", "url": url},
                [{"id": "11", "text": "Useful comment", "user": "A"}],
                {"status": "partial", "fetched_comments": 1,
                 "truncated_by": ["comment_page_empty"], "errors": []})

    monkeypatch.setattr(csdn, "fetch_discussion", fake_fetch)
    row = {
        "source": "csdn_search",
        "title": "Search result title",
        "url": ARTICLE_URL,
        "text": "Short search excerpt",
        "author": "Search author",
    }

    merged, stat, patches = topic_enrichment._enrich_topk([row], budget_seconds=5)

    assert merged[0]["text"] == "Full body"
    assert merged[0]["comments"][0]["text"] == "Useful comment"
    assert merged[0]["discussion_coverage"]["status"] == "partial"
    assert patches[ARTICLE_URL]["discussion_coverage"]["truncated_by"] == [
        "comment_page_empty"]
    assert received["url"] == ARTICLE_URL
    assert 0 < received["budget"] <= 5
    assert stat["csdn_discussion_targets"] == 1
    assert stat["csdn_discussion_partial"] == 1
