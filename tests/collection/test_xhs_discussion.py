from __future__ import annotations

from periscope.collection.platforms import xhs
from periscope.topics import enrichment as topic_enrichment


NOTE_ID = "66a1b2c3000000000a012345"
NOTE_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=example&xsec_source=pc_search"


def test_note_identity_accepts_explore_and_search_result_urls():
    assert xhs._note_id(NOTE_URL) == NOTE_ID
    assert xhs._note_id(f"https://www.xiaohongshu.com/search_result/{NOTE_ID}?xsec_token=x") == NOTE_ID
    assert xhs._note_id("https://example.org/explore/123") == ""


def test_normalizes_inline_comment_replies_and_marks_preview_only():
    payload = {"data": {"comments": [{
        "id": "root-1", "note_id": NOTE_ID, "content": "Root comment",
        "like_count": 5, "sub_comment_count": 3,
        "sub_comments": [{"id": "reply-1", "note_id": NOTE_ID,
                          "content": "Nested reply", "user_info": {"nickname": "reader"}}],
        "user_info": {"nickname": "author"},
    }], "cursor": "", "has_more": False}}

    detail, comments, coverage = xhs._parse_comment_pages(
        [payload], note_id=NOTE_ID, note_url=NOTE_URL, max_comments=10)

    assert detail == {}
    assert comments[0]["text"] == "Root comment"
    assert comments[0]["replies"][0]["text"] == "Nested reply"
    assert comments[0]["replies"][0]["parent_id"] == "root-1"
    assert "xsec_token" not in comments[0]["url"]
    assert coverage["fetched_roots"] == 1
    assert coverage["fetched_replies"] == 1
    assert coverage["status"] == "partial"
    assert "reply_preview_only" in coverage["truncated_by"]


def test_invalid_comment_payload_is_not_reported_as_complete():
    _, comments, coverage = xhs._parse_comment_pages(
        [{"data": {"cursor": "", "has_more": False}}], note_id=NOTE_ID,
        note_url=NOTE_URL, max_comments=10)

    assert comments == []
    assert coverage["status"] == "partial"
    assert "comments:invalid_payload" in coverage["errors"]


def test_enrichment_uses_single_group_fetch_and_patches_raw_row(monkeypatch):
    received = {}

    async def fake_fetch_many(items, *, max_comments, budget_seconds):
        received["items"] = items
        received["max_comments"] = max_comments
        received["budget"] = budget_seconds
        return {NOTE_URL: ({"note_id": NOTE_ID, "text": "Full note body", "author": "creator"},
                           [{"id": "root-1", "text": "Useful comment"}],
                           {"status": "partial", "fetched_comments": 1,
                            "fetched_roots": 1, "fetched_replies": 0,
                            "truncated_by": ["reply_preview_only"], "errors": []})}

    monkeypatch.setattr(xhs, "fetch_many", fake_fetch_many)
    row = {"source": "xhs_explore", "title": "Search card", "url": NOTE_URL,
           "text": "Search card", "author": "", "ids": {"note_id": NOTE_ID}}

    merged, stat, patches = topic_enrichment._enrich_topk([row], budget_seconds=9)

    assert merged[0]["text"] == "Full note body"
    assert merged[0]["comments"][0]["text"] == "Useful comment"
    assert merged[0]["discussion_coverage"]["status"] == "partial"
    assert patches[NOTE_URL]["ids"]["note_id"] == NOTE_ID
    assert stat["xhs_discussion_targets"] == 1
    assert stat["xhs_discussion_done"] == 1
    assert stat["targets"] == 1
    assert received["max_comments"] == 60
    assert received["items"][0]["note_id"] == NOTE_ID
    assert 0 < received["budget"] <= 9
