from __future__ import annotations

from periscope.collection.platforms import douyin
from periscope.topics import enrichment as topic_enrichment


AWEME_ID = "7350000000000000000"
VIDEO_URL = f"https://www.douyin.com/video/{AWEME_ID}?from=search"


def test_video_identity_only_accepts_douyin_video_urls():
    assert douyin._aweme_id(VIDEO_URL) == AWEME_ID
    assert douyin._aweme_id("https://example.org/video/123") == ""


def test_normalizes_comment_page_and_reports_unfetched_replies_and_more_pages():
    payload = {"status_code": 0, "cursor": 5, "has_more": 1, "total": 70,
               "comments": [{
                   "cid": "root-1", "text": "Useful comment", "aweme_id": AWEME_ID,
                   "create_time": 1700000000, "digg_count": 4,
                   "user": {"nickname": "reader"}, "reply_comment_total": 3,
                   "reply_comment": None,
               }]}

    detail, comments, coverage = douyin._parse_comment_pages(
        [payload], aweme_id=AWEME_ID, max_comments=60)

    assert detail == {}
    assert comments[0]["text"] == "Useful comment"
    assert comments[0]["author"] == "reader"
    assert comments[0]["url"] == f"https://www.douyin.com/video/{AWEME_ID}"
    assert comments[0].get("replies", []) == []
    assert coverage["reported_comments"] == 70
    assert coverage["reported_replies"] == 3
    assert coverage["status"] == "partial"
    assert "comment_page_limit" in coverage["truncated_by"]
    assert "reply_threads_not_fetched" in coverage["truncated_by"]


def test_topic_enrichment_batches_douyin_comments_and_patches_raw_row(monkeypatch):
    received = {}

    async def fake_fetch_many(items, *, max_comments, budget_seconds):
        received["items"] = items
        received["limit"] = max_comments
        received["budget"] = budget_seconds
        return {VIDEO_URL: ({}, [{"id": "root-1", "text": "Useful comment"}],
                            {"status": "partial", "fetched_comments": 1,
                             "fetched_roots": 1, "fetched_replies": 0,
                             "truncated_by": ["reply_threads_not_fetched"], "errors": []})}

    monkeypatch.setattr(douyin, "fetch_many", fake_fetch_many)
    row = {"source": "douyin_hot", "title": "Video caption", "url": VIDEO_URL,
           "text": "Search description", "ids": {"aweme_id": AWEME_ID}}

    merged, stat, patches = topic_enrichment._enrich_topk([row], budget_seconds=9)

    assert merged[0]["text"] == "Search description"
    assert merged[0]["comments"][0]["text"] == "Useful comment"
    assert merged[0]["discussion_coverage"]["status"] == "partial"
    assert patches[VIDEO_URL]["ids"]["aweme_id"] == AWEME_ID
    assert stat["douyin_discussion_targets"] == 1
    assert stat["douyin_discussion_done"] == 1
    assert stat["targets"] == 1
    assert received["items"][0]["aweme_id"] == AWEME_ID
    assert received["limit"] == 60
    assert 0 < received["budget"] <= 9
