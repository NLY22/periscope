from __future__ import annotations

import asyncio

import httpx

from periscope.collection.platforms import weibo
from periscope.topics import enrichment as topic_enrichment


POST_ID = "5100000000000000"
POST_URL = "https://weibo.com/detail/P63M7nZuG"


def _run(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return asyncio.run(weibo.fetch_discussion(POST_URL, post_id=POST_ID,
                                                  client=client, **kwargs))
    finally:
        asyncio.run(client.aclose())


def test_fetches_status_and_nested_comments():
    def handler(request):
        if request.url.path.endswith("/statuses/show"):
            return httpx.Response(200, json={"ok": 1, "idstr": POST_ID,
                "text_raw": "Full status text", "comments_count": 1,
                "user": {"screen_name": "author"}})
        return httpx.Response(200, json={"ok": 1, "data": [{
            "id": 101, "text": "Root comment", "like_count": 4,
            "user": {"screen_name": "reader"}, "comments": [{
                "id": 102, "rootid": 101, "text": "Nested reply",
                "user": {"screen_name": "responder"},
            }],
        }], "total_number": 1, "max_id": 0, "max_id_type": 0})

    detail, comments, coverage = _run(handler, budget_seconds=5)

    assert detail["text"] == "Full status text"
    assert detail["author"] == "author"
    assert comments[0]["text"] == "Root comment"
    assert comments[0]["replies"][0]["text"] == "Nested reply"
    assert comments[0]["replies"][0]["parent_id"] == "101"
    assert coverage["status"] == "complete"
    assert coverage["fetched_roots"] == 1
    assert coverage["fetched_replies"] == 1


def test_login_gate_preserves_coverage_and_does_not_claim_body():
    def handler(request):
        return httpx.Response(200, text="<title>Sina Visitor System</title>",
                              headers={"content-type": "text/html"})

    detail, comments, coverage = _run(handler, budget_seconds=5)

    assert detail == {"post_id": POST_ID, "url": POST_URL, "text": ""}
    assert comments == []
    assert coverage["status"] == "partial"
    assert coverage["errors"] == ["status:login_required", "comments:login_required"]


def test_weibo_visitor_json_is_reported_as_login_required():
    def handler(request):
        return httpx.Response(200, json={"ok": -100,
            "url": "https://passport.weibo.com/visitor/visitor"})

    _, _, coverage = _run(handler, budget_seconds=5)

    assert coverage["errors"] == ["status:login_required", "comments:login_required"]


def test_comment_limit_marks_partial_and_keeps_roots():
    def handler(request):
        if request.url.path.endswith("/statuses/show"):
            return httpx.Response(200, json={"ok": 1, "idstr": POST_ID,
                "text_raw": "Status", "comments_count": 12})
        return httpx.Response(200, json={"ok": 1, "data": [
            {"id": 101, "text": "First"}, {"id": 102, "text": "Second"},
        ], "total_number": 12, "max_id": 50, "max_id_type": 0})

    _, comments, coverage = _run(handler, max_comments=1, budget_seconds=5)

    assert len(comments) == 1
    assert coverage["status"] == "partial"
    assert coverage["reported_comments"] == 12
    assert "comment_limit" in coverage["truncated_by"]


def test_topic_enrichment_attaches_weibo_materials(monkeypatch):
    async def fake_fetch(post_url, *, post_id, budget_seconds):
        assert post_id == POST_ID
        return ({"post_id": POST_ID, "text": "Full status", "author": "author"},
                [{"id": "c1", "text": "Comment"}],
                {"status": "partial", "fetched_comments": 1,
                 "truncated_by": ["comment_limit"], "errors": []})

    monkeypatch.setattr(weibo, "fetch_discussion", fake_fetch)
    row = {"source": "weibo_search", "title": "Short result", "url": POST_URL,
           "text": "Excerpt", "author": "", "ids": {"id": POST_ID}}

    merged, stat, patches = topic_enrichment._enrich_topk([row], budget_seconds=5)

    assert merged[0]["text"] == "Full status"
    assert merged[0]["comments"][0]["text"] == "Comment"
    assert merged[0]["discussion_coverage"]["status"] == "partial"
    assert patches[POST_URL]["ids"]["post_id"] == POST_ID
    assert stat["weibo_discussion_targets"] == 1
    assert stat["weibo_discussion_done"] == 1
