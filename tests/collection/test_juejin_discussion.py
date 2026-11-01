from __future__ import annotations

import asyncio

import httpx

from periscope.collection.platforms import juejin
from periscope.collection.transport.parsing import extract_rows
from periscope.collection.catalog.community import SOURCES as COMMUNITY_SOURCES
from periscope.topics import enrichment as topic_enrichment


ARTICLE_ID = "6844903896637259784"
ARTICLE_URL = f"https://juejin.cn/post/{ARTICLE_ID}"


def _comment(comment_id: str, text: str, *, reply=None):
    info = {
        "comment_id": comment_id,
        "item_id": ARTICLE_ID,
        "comment_content": text,
        "reply_count": 1 if reply else 0,
        "digg_count": 3,
        "ctime": 1_700_000_000,
    }
    if reply:
        info["comment_replys"] = [reply]
    return {
        "comment_id": comment_id,
        "comment_info": info,
        "user_info": {"user_name": "reader"},
    }


def _run(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return asyncio.run(juejin.fetch_discussion(ARTICLE_URL, client=client, **kwargs))
    finally:
        asyncio.run(client.aclose())


def test_fetches_full_article_and_preserves_embedded_reply_parent():
    reply = {
        "reply_id": "reply-1",
        "reply_comment_id": "comment-1",
        "user_id": "user-2",
        "reply_content": "Nested answer",
        "ctime": 1_700_000_001,
    }

    def handler(request):
        if request.url.path.endswith("/article/detail"):
            return httpx.Response(200, json={"err_no": 0, "data": {
                "article_info": {
                    "article_id": ARTICLE_ID,
                    "title": "Full article",
                    "content": "<h2>Body</h2><p>Full text here.</p>",
                    "comment_count": 1,
                },
                "author_user_info": {"user_name": "author"},
            }})
        return httpx.Response(200, json={
            "err_no": 0,
            "data": [_comment("comment-1", "Root comment", reply=reply)],
            "cursor": "1",
            "count": 1,
            "has_more": False,
        })

    detail, comments, coverage = _run(handler, budget_seconds=5)

    assert detail["title"] == "Full article"
    assert detail["text"] == "Body\nFull text here."
    assert detail["author"] == "author"
    assert comments[0]["text"] == "Root comment"
    assert comments[0]["replies"][0]["text"] == "Nested answer"
    assert comments[0]["replies"][0]["parent_id"] == "comment-1"
    assert coverage["status"] == "complete"
    assert coverage["fetched_comments"] == 2
    assert coverage["fetched_roots"] == 1
    assert coverage["fetched_replies"] == 1


def test_comment_limit_keeps_partial_rows_and_reports_coverage():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/article/detail"):
            return httpx.Response(200, json={"err_no": 0, "data": {
                "article_info": {"article_id": ARTICLE_ID, "content": "<p>Article</p>",
                                 "comment_count": 8},
            }})
        return httpx.Response(200, json={"err_no": 0,
            "data": [_comment("1", "one"), _comment("2", "two")],
            "cursor": "2", "count": 8, "has_more": True})

    detail, comments, coverage = _run(handler, max_comments=2, budget_seconds=5)

    assert detail["text"] == "Article"
    assert [item["text"] for item in comments] == ["one", "two"]
    assert coverage["status"] == "partial"
    assert coverage["reported_comments"] == 8
    assert "comment_limit" in coverage["truncated_by"]
    assert len(calls) == 2


def test_juejin_search_rows_have_canonical_urls_and_article_ids():
    source = next(source for source in COMMUNITY_SOURCES if source.key == "juejin_search")
    payload = {"data": [{"result_model": {
        "article_info": {"article_id": ARTICLE_ID, "title": "Example", "brief_content": "Brief"},
        "author_user_info": {"user_name": "author"},
    }}]}

    rows = extract_rows(source, __import__("json").dumps(payload), "application/json",
                        base_url="https://api.juejin.cn/search_api/v1/search")

    assert rows[0]["url"] == ARTICLE_URL
    assert rows[0]["ids"]["result_model.article_info.article_id"] == ARTICLE_ID


def test_topic_enrichment_attaches_juejin_detail_and_comments(monkeypatch):
    async def fake_fetch(article_url, *, budget_seconds):
        return ({"article_id": ARTICLE_ID, "title": "Full", "text": "Full body",
                 "author": "author", "url": article_url},
                [{"id": "comment-1", "text": "Useful", "user": "reader"}],
                {"status": "partial", "fetched_comments": 1,
                 "truncated_by": ["comment_limit"], "errors": []})

    monkeypatch.setattr(juejin, "fetch_discussion", fake_fetch)
    row = {"source": "juejin_search", "title": "Excerpt", "url": ARTICLE_URL,
           "text": "Brief text", "author": ""}

    merged, stat, patches = topic_enrichment._enrich_topk([row], budget_seconds=5)

    assert merged[0]["text"] == "Full body"
    assert merged[0]["comments"][0]["text"] == "Useful"
    assert merged[0]["discussion_coverage"]["status"] == "partial"
    assert patches[ARTICLE_URL]["discussion_coverage"]["truncated_by"] == ["comment_limit"]
    assert stat["juejin_discussion_targets"] == 1
    assert stat["juejin_discussion_done"] == 1
    assert stat["juejin_discussion_partial"] == 1
