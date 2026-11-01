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


def test_expanded_sub_comment_pages_complete_reply_preview_and_keep_parent():
    root_payload = {"data": {"comments": [{
        "id": "root-2", "content": "Root with replies", "sub_comment_count": 3,
        "sub_comments": [{"id": "reply-1", "content": "Preview reply"}],
    }], "cursor": "", "has_more": False}}
    reply_payload = {"data": {"comments": [
        {"id": "reply-1", "content": "Preview reply"},
        {"id": "reply-2", "content": "Second reply"},
    ], "cursor": "reply-page", "has_more": True}}
    reply_payload_2 = {"data": {"comments": [
        {"id": "reply-3", "content": "Third reply"},
    ], "cursor": "", "has_more": False}}

    _, comments, coverage = xhs._parse_comment_pages(
        [root_payload], note_id=NOTE_ID, note_url=NOTE_URL, max_comments=10,
        sub_payloads=[{"root_comment_id": "root-2", "payload": reply_payload},
                      {"root_comment_id": "root-2", "payload": reply_payload_2}])

    assert [reply["id"] for reply in comments[0]["replies"]] == [
        "reply-1", "reply-2", "reply-3"]
    assert all(reply["parent_id"] == "root-2" for reply in comments[0]["replies"])
    assert coverage["fetched_replies"] == 3
    assert coverage["reported_replies"] == 3
    assert coverage["reply_pages_attempted"] == 2
    assert coverage["status"] == "complete"
    assert "reply_preview_only" not in coverage["truncated_by"]
    assert "reply_page_limit" not in coverage["truncated_by"]


def test_expanded_sub_comment_parser_bounds_reply_threads():
    roots = [{"id": f"root-{index}", "content": "Root", "sub_comment_count": 1}
             for index in range(1, 7)]
    root_payload = {"data": {"comments": roots, "cursor": "", "has_more": False}}
    reply_payloads = [{"root_comment_id": f"root-{index}", "payload": {"data": {
        "comments": [{"id": f"reply-{index}", "content": "Reply"}],
        "has_more": False,
    }}} for index in range(1, 7)]

    _, comments, coverage = xhs._parse_comment_pages(
        [root_payload], note_id=NOTE_ID, note_url=NOTE_URL, max_comments=60,
        sub_payloads=reply_payloads)

    assert sum(len(comment["replies"]) for comment in comments) == 5
    assert coverage["reply_pages_attempted"] == 5
    assert "reply_thread_limit" in coverage["truncated_by"]
    assert coverage["status"] == "partial"


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
