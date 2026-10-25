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


def test_normalizes_expanded_reply_pages_and_keeps_root_relationship():
    root_payload = {"status_code": 0, "cursor": 0, "has_more": 0, "total": 1,
                    "comments": [{
                        "cid": "root-1", "text": "Root", "reply_comment_total": 4,
                        "reply_comment": [{"cid": "reply-1", "text": "Preview"}],
                    }]}
    reply_page = {"status_code": 0, "cursor": 2, "has_more": 1, "total": 4,
                  "comments": [
                      {"cid": "reply-1", "text": "Preview", "root_comment_id": "root-1"},
                      {"cid": "reply-2", "text": "Expanded", "root_comment_id": "root-1"},
                  ]}
    reply_page_2 = {"status_code": 0, "cursor": 0, "has_more": 0, "total": 4,
                    "comments": [{"cid": "reply-3", "text": "Last page",
                                  "root_comment_id": "root-1"}]}

    _, comments, coverage = douyin._parse_comment_pages(
        [root_payload], aweme_id=AWEME_ID, max_comments=60,
        reply_payloads=[{"comment_id": "root-1", "payload": reply_page},
                        {"comment_id": "root-1", "payload": reply_page_2}])

    assert [reply["id"] for reply in comments[0]["replies"]] == [
        "reply-1", "reply-2", "reply-3"]
    assert all(reply["parent_id"] == "root-1" for reply in comments[0]["replies"])
    assert coverage["fetched_replies"] == 3
    assert coverage["reply_pages_attempted"] == 2
    assert coverage["status"] == "partial"
    assert "reply_thread_incomplete" in coverage["truncated_by"]
    assert "reply_page_limit" not in coverage["truncated_by"]


def test_expanded_reply_has_more_is_reported_as_page_limit():
    root_payload = {"status_code": 0, "cursor": 0, "has_more": 0, "total": 1,
                    "comments": [{"cid": "root-1", "text": "Root",
                                  "reply_comment_total": 10}]}
    reply_page = {"status_code": 0, "cursor": 3, "has_more": 1, "total": 10,
                  "comments": [{"cid": "reply-1", "text": "Reply",
                                "root_comment_id": "root-1"}]}

    _, _, coverage = douyin._parse_comment_pages(
        [root_payload], aweme_id=AWEME_ID,
        reply_payloads=[{"comment_id": "root-1", "payload": reply_page}])

    assert coverage["status"] == "partial"
    assert "reply_page_limit" in coverage["truncated_by"]


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
