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


def test_fetches_reply_pages_with_fetch_level_and_cursor():
    reply_requests = []

    def handler(request):
        if request.url.path.endswith("/statuses/show"):
            return httpx.Response(200, json={"ok": 1, "idstr": POST_ID,
                "text_raw": "Status", "comments_count": 1})
        params = request.url.params
        if params.get("fetch_level") == "1":
            reply_requests.append(dict(params))
            if params.get("is_mix") == "0":
                return httpx.Response(200, json={"ok": 1, "data": [
                    {"id": 102, "rootid": 101, "text": "Embedded duplicate"},
                    {"id": 103, "rootid": 101, "text": "Second reply"},
                ], "total_number": 3, "max_id": 42, "max_id_type": 1})
            return httpx.Response(200, json={"ok": 1, "data": [
                {"id": 104, "text": "Third reply"},
            ], "total_number": 3, "max_id": 0, "max_id_type": 0})
        return httpx.Response(200, json={"ok": 1, "data": [{
            "id": 101, "text": "Root", "comments": [
                {"id": 102, "rootid": 101, "text": "Embedded duplicate"},
            ],
        }], "total_number": 1, "max_id": 0, "max_id_type": 0})

    _, comments, coverage = _run(handler, budget_seconds=5)

    assert [r["text"] for r in comments[0]["replies"]] == [
        "Embedded duplicate", "Second reply", "Third reply"]
    assert all(r["parent_id"] == "101" for r in comments[0]["replies"])
    assert [r["id"] for r in comments[0]["replies"]].count("102") == 1
    assert coverage["reported_replies"] == 3
    assert coverage["fetched_replies"] == 3
    assert coverage["fetched_comments"] == 4
    assert coverage["reply_threads_attempted"] == 1
    assert coverage["reply_pages_attempted"] == 2
    assert coverage["status"] == "complete"
    assert len(reply_requests) == 2
    assert reply_requests[0]["fetch_level"] == "1"
    assert reply_requests[0]["is_mix"] == "0"
    assert reply_requests[1]["is_mix"] == "1"
    assert reply_requests[1]["max_id"] == "42"


def test_reply_count_mismatch_marks_partial():
    def handler(request):
        if request.url.path.endswith("/statuses/show"):
            return httpx.Response(200, json={"ok": 1, "idstr": POST_ID,
                "text_raw": "Status"})
        if request.url.params.get("fetch_level") == "1":
            return httpx.Response(200, json={"ok": 1, "data": [
                {"id": 102, "rootid": 101, "text": "First reply"},
                {"id": 103, "rootid": 101, "text": "Second reply"},
            ], "total_number": 3, "max_id": 0})
        return httpx.Response(200, json={"ok": 1, "data": [
            {"id": 101, "text": "Root"},
        ], "total_number": 1, "max_id": 0})

    _, comments, coverage = _run(handler, budget_seconds=5)

    assert len(comments[0]["replies"]) == 2
    assert coverage["reported_replies"] == 3
    assert coverage["fetched_replies"] == 2
    assert coverage["status"] == "partial"
    assert "reply_count_mismatch" in coverage["truncated_by"]


def test_reply_collection_respects_thread_page_and_node_limits(monkeypatch):
    monkeypatch.setattr(weibo, "MAX_REPLY_THREADS", 2)
    monkeypatch.setattr(weibo, "MAX_REPLY_PAGES", 2)
    reply_requests = []

    def handler(request):
        if request.url.path.endswith("/statuses/show"):
            return httpx.Response(200, json={"ok": 1, "idstr": POST_ID,
                "text_raw": "Status"})
        params = request.url.params
        if params.get("fetch_level") == "1":
            reply_requests.append(dict(params))
            return httpx.Response(200, json={"ok": 1, "data": [
                {"id": "duplicate-%s" % params.get("id"),
                 "rootid": params.get("id"), "text": "Already embedded"},
            ], "total_number": 4, "max_id": len(reply_requests), "max_id_type": 1})
        return httpx.Response(200, json={"ok": 1, "data": [
            {"id": 101, "text": "Root one", "comments": [
                {"id": "duplicate-101", "rootid": 101, "text": "Already embedded"}]},
            {"id": 102, "text": "Root two", "comments": [
                {"id": "duplicate-102", "rootid": 102, "text": "Already embedded"}]},
            {"id": 103, "text": "Root three"},
        ], "total_number": 3, "max_id": 0})

    _, _, coverage = _run(handler, max_comments=8, budget_seconds=5)

    assert coverage["fetched_comments"] <= 8
    assert coverage["reply_threads_attempted"] == 2
    assert coverage["reply_pages_attempted"] <= 4
    assert len(reply_requests) <= 4
    assert "reply_page_limit" in coverage["truncated_by"]
    assert "reply_thread_limit" in coverage["truncated_by"]


def test_reply_time_budget_keeps_partial_material(monkeypatch):
    original = weibo._get_json

    async def stop_on_reply(client, url, *, params, headers, deadline):
        if params.get("fetch_level") == 1:
            return None, "time_budget"
        return await original(client, url, params=params, headers=headers,
                              deadline=deadline)

    monkeypatch.setattr(weibo, "_get_json", stop_on_reply)

    def handler(request):
        if request.url.path.endswith("/statuses/show"):
            return httpx.Response(200, json={"ok": 1, "idstr": POST_ID,
                "text_raw": "Status"})
        return httpx.Response(200, json={"ok": 1, "data": [
            {"id": 101, "text": "Preserved root"},
        ], "total_number": 1, "max_id": 0})

    _, comments, coverage = _run(handler, budget_seconds=5)

    assert comments[0]["text"] == "Preserved root"
    assert coverage["status"] == "partial"
    assert "time_budget" in coverage["truncated_by"]


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
