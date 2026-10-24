from __future__ import annotations

import asyncio
import httpx

from periscope.collection.platforms import zhihu
from periscope.topics import enrichment as topic_enrichment


ANSWER_ID = "1982145400350713779"
ANSWER_URL = f"https://www.zhihu.com/question/554751065/answer/{ANSWER_ID}"


def _run(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return asyncio.run(zhihu.fetch_discussion(ANSWER_URL, client=client, **kwargs))
    finally:
        asyncio.run(client.aclose())


def test_fetches_answer_and_builds_comment_reply_tree():
    def handler(request):
        if request.url.path == f"/api/v4/answers/{ANSWER_ID}":
            return httpx.Response(200, json={"id": ANSWER_ID, "content": "<p>Full answer.</p>",
                "comment_count": 2, "author": {"name": "author"}})
        return httpx.Response(200, json={"data": [
            {"id": "root-1", "content": "Root comment", "created_time": 1700000000,
             "author": {"name": "reader"}},
            {"id": "reply-1", "content": "Reply comment", "created_time": 1700000001,
             "author": {"name": "responder"}, "reply_to_comment": {"id": "root-1"}},
        ], "paging": {"totals": 2, "is_end": True}})

    detail, comments, coverage = _run(handler, budget_seconds=5)

    assert detail["text"] == "Full answer."
    assert detail["author"] == "author"
    assert comments[0]["id"] == "root-1"
    assert comments[0]["replies"][0]["id"] == "reply-1"
    assert comments[0]["replies"][0]["parent_id"] == "root-1"
    assert coverage["status"] == "complete"
    assert coverage["fetched_comments"] == 2


def test_answer_login_requirement_does_not_discard_public_comments():
    def handler(request):
        if request.url.path == f"/api/v4/answers/{ANSWER_ID}":
            return httpx.Response(403, json={"error": {"need_login": True}})
        return httpx.Response(200, json={"data": [
            {"id": "root-1", "content": "Public comment", "author": {"name": "reader"}},
        ], "paging": {"totals": 1, "is_end": True}})

    detail, comments, coverage = _run(handler, budget_seconds=5)

    assert detail["text"] == ""
    assert comments[0]["text"] == "Public comment"
    assert coverage["status"] == "partial"
    assert coverage["errors"] == ["answer:login_required"]


def test_comment_limit_keeps_partial_results():
    def handler(request):
        if request.url.path == f"/api/v4/answers/{ANSWER_ID}":
            return httpx.Response(200, json={"id": ANSWER_ID, "content": "Body"})
        return httpx.Response(200, json={"data": [
            {"id": "one", "content": "One"}, {"id": "two", "content": "Two"},
        ], "paging": {"totals": 8, "is_end": False}})

    _, comments, coverage = _run(handler, max_comments=1, budget_seconds=5)

    assert len(comments) == 1
    assert coverage["status"] == "partial"
    assert coverage["reported_comments"] == 8
    assert "comment_limit" in coverage["truncated_by"]


def test_topic_enrichment_attaches_zhihu_answer_material(monkeypatch):
    async def fake_fetch(answer_url, *, budget_seconds):
        return ({"answer_id": ANSWER_ID, "text": "Full answer", "author": "author"},
                [{"id": "c1", "text": "Useful discussion"}],
                {"status": "partial", "fetched_comments": 1,
                 "truncated_by": ["comment_limit"], "errors": []})

    monkeypatch.setattr(zhihu, "fetch_discussion", fake_fetch)
    row = {"source": "zhihu_search", "title": "Question", "url": ANSWER_URL,
           "text": "Excerpt", "author": ""}

    merged, stat, patches = topic_enrichment._enrich_topk([row], budget_seconds=5)

    assert merged[0]["text"] == "Full answer"
    assert merged[0]["comments"][0]["text"] == "Useful discussion"
    assert merged[0]["discussion_coverage"]["status"] == "partial"
    assert patches[ANSWER_URL]["ids"]["answer_id"] == ANSWER_ID
    assert stat["zhihu_discussion_targets"] == 1
    assert stat["zhihu_discussion_done"] == 1


def test_question_and_non_zhihu_urls_are_not_fetched():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        detail, comments, coverage = asyncio.run(zhihu.fetch_discussion(
            "https://example.org/answer/123", client=client))
    finally:
        asyncio.run(client.aclose())

    assert calls == []
    assert detail == {}
    assert comments == []
    assert coverage["errors"] == ["invalid_answer_url"]
