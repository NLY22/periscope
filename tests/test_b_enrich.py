"""B站富化: counters always, transcript when the platform allows it.

Two contracts are pinned here because both were wrong on the first attempt:

- an empty subtitle list from `player/v2` does **not** mean the video has no
  subtitles (measured: 80 anonymous probes, every one empty), so the failure
  has to be reported with a reason instead of as "none";
- the merge copies rows, so a patch applied to the merged view never reaches
  the run shard. The module under test is the adapter, but the patch shape it
  produces is what the caller applies to originals.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from collectors import bilibili_enrich as be


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


class _Client:
    """Minimal stand-in for httpx.AsyncClient: routes by URL fragment."""

    def __init__(self, view, player, subtitle=None, reply=None,
                 reply_pages=None, subreply=None):
        self.view = view
        self.player = player
        self.subtitle = subtitle
        self.reply = reply
        self.reply_pages = reply_pages or {}
        self.subreply = subreply or {}
        self.calls = []

    async def get(self, url, params=None, headers=None, follow_redirects=True):
        self.calls.append((url, params))
        if "web-interface/view" in url:
            return _Resp(self.view)
        if "player" in url:
            return _Resp(self.player)
        if "v2/reply/reply" in url:
            root = (params or {}).get("root")
            return _Resp(self.subreply.get(root, {"code": 0, "data": {"replies": []}}))
        if "v2/reply" in url or "/reply" in url:
            page = (params or {}).get("pn", 1)
            if page in self.reply_pages:
                return _Resp(self.reply_pages[page])
            return _Resp(self.reply or {"code": 0, "data": {"replies": []}})
        return _Resp(self.subtitle or {"body": []})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _view(stat=None, cid=42, aid=7):
    return {"code": 0, "data": {
        "aid": aid,
        "pages": [{"cid": cid}],
        "stat": stat or {"play": 100, "like": 9, "favorite": 3, "coin": 2,
                         "danmaku": 1, "reply": 4, "share": 0},
    }}


def _run(monkeypatch, client, **kw):
    monkeypatch.setattr(be.httpx, "AsyncClient", lambda *a, **k: client)
    return asyncio.run(be.enrich("BV1xxxxxxxxx", **kw))


def test_counters_are_collected_with_the_platforms_own_names(monkeypatch):
    client = _Client(_view(), {"code": 0, "data": {"subtitle": {"subtitles": []}}})
    got = _run(monkeypatch, client)
    assert got.metrics["play"] == 100
    assert got.metrics["favorites"] == 3      # B站 says `favorite`, plural kept
    assert got.metrics["coin"] == 2
    assert got.cid == 42


def test_missing_counter_stays_missing(monkeypatch):
    client = _Client(_view(stat={"play": 5}), {"code": 0, "data": {}})
    got = _run(monkeypatch, client)
    assert got.metrics == {"play": 5}
    assert "like" not in got.metrics, "an absent counter is not a zero"


def test_empty_track_list_is_reported_as_unavailable_not_as_none(monkeypatch):
    client = _Client(_view(), {"code": 0, "data": {"subtitle": {"subtitles": []}}})
    got = _run(monkeypatch, client)
    assert got.subtitle_locked is True
    assert any("subtitle:" in e for e in got.errors)
    assert got.has_transcript is False


def test_need_login_is_named_explicitly(monkeypatch):
    client = _Client(_view(), {"code": 0, "data": {
        "need_login_subtitle": True, "subtitle": {"subtitles": []}}})
    got = _run(monkeypatch, client)
    assert "subtitle:need-login" in got.errors


def test_human_track_wins_over_ai(monkeypatch):
    player = {"code": 0, "data": {"subtitle": {"subtitles": [
        {"lan": "ai-zh", "ai_status": 2, "subtitle_url": "//subs/ai.json"},
        {"lan": "zh-CN", "subtitle_url": "//subs/human.json"},
    ]}}}
    subtitle = {"body": [
        {"from": 0, "to": 1.0, "content": "第一句"},
        {"from": 1.0, "to": 2.0, "content": "第二句"},
    ]}
    got = _run(monkeypatch, _Client(_view(), player, subtitle=subtitle))
    assert got.transcript_method == "cc"
    assert got.text == "第一句 第二句"
    assert got.segments[1]["text"] == "第二句"


def test_ai_only_is_not_used_as_a_transcript(monkeypatch):
    player = {"code": 0, "data": {"subtitle": {"subtitles": [
        {"lan": "ai-zh", "ai_status": 2, "subtitle_url": "//subs/ai.json"},
    ]}}}
    got = _run(monkeypatch, _Client(_view(), player, subtitle={"body": []}))
    assert got.has_transcript is False, (
        "an ASR artifact presented as the creator's words is the confusion the "
        "method column exists to prevent")


def test_view_failure_stops_early_without_cascading(monkeypatch):
    class Boom(_Client):
        async def get(self, url, **kw):
            if "web-interface/view" in url:
                raise httpx.ConnectError("down")
            return await super().get(url, **kw)

    got = _run(monkeypatch, Boom(_view(), {"code": 0, "data": {}}))
    assert got.errors and got.errors[0].startswith("view:")
    assert got.metrics == {}


def test_comments_are_off_unless_asked(monkeypatch):
    reply = {"code": 0, "data": {"replies": [
        {"content": {"message": "评论一"}, "member": {"uname": "某人"}, "like": 3}]}}
    quiet = _run(monkeypatch, _Client(_view(), {"code": 0, "data": {}}, reply=reply))
    assert quiet.comments == [], "comments are a lead, not the author's words"

    loud = _run(monkeypatch, _Client(_view(), {"code": 0, "data": {}}, reply=reply),
                with_comments=True)
    assert loud.comments[0]["text"] == "评论一"
    assert loud.comments[0]["likes"] == 3


def test_captured_comments_keep_their_full_text_and_already_returned_replies(monkeypatch):
    text = "Full captured comment " * 30 + "tailcommentneedle"
    reply = {"code": 0, "data": {"replies": [
        {"rpid": 1, "content": {"message": text}, "member": {"uname": "Parent"},
         "replies": [{"rpid": 2, "parent": 1, "content": {"message": "nestedcapturedreply"},
                       "member": {"uname": "Child"}}]}]}}
    result = _run(monkeypatch, _Client(_view(), {"code": 0, "data": {}}, reply=reply), with_comments=True)
    assert result.comments[0]["text"] == text
    assert result.comments[0]["replies"][0]["text"] == "nestedcapturedreply"
    assert result.comments[0]["replies"][0]["parent_id"] == 1


def test_comments_follow_top_level_pages_and_report_coverage(monkeypatch):
    first = {"code": 0, "data": {"page": {"count": 2}, "replies": [
        {"rpid": 1, "content": {"message": "first"}, "member": {"uname": "A"}}]}}
    second = {"code": 0, "data": {"page": {"count": 2}, "replies": [
        {"rpid": 2, "content": {"message": "second"}, "member": {"uname": "B"}}]}}
    client = _Client(_view(), {"code": 0, "data": {}}, reply_pages={1: first, 2: second})

    got = _run(monkeypatch, client, with_comments=True, comment_limit=2)

    assert [comment["text"] for comment in got.comments] == ["first", "second"]
    coverage = got.as_patch()["discussion_coverage"]
    assert coverage["fetched_comments"] == 2
    assert coverage["reported_comments"] == 2
    assert coverage["status"] == "complete"
    assert [call[1]["pn"] for call in client.calls if "v2/reply" in call[0]] == [1, 2]


def test_empty_later_page_does_not_erase_a_larger_reported_count(monkeypatch):
    first = {"code": 0, "data": {"page": {"count": 5}, "replies": [
        {"rpid": 1, "content": {"message": "one"}, "member": {"uname": "A"}},
        {"rpid": 2, "content": {"message": "two"}, "member": {"uname": "B"}}]}}
    second = {"code": 0, "data": {"page": {"count": 0}, "replies": []}}
    client = _Client(_view(), {"code": 0, "data": {}}, reply_pages={1: first, 2: second})

    got = _run(monkeypatch, client, with_comments=True, comment_limit=10)

    coverage = got.as_patch()["discussion_coverage"]
    assert coverage["reported_comments"] == 5
    assert coverage["status"] == "partial"
    assert "comment_page_empty" in coverage["truncated_by"]


def test_reaching_node_limit_without_reported_count_is_marked_partial(monkeypatch):
    comments = [{"rpid": i, "content": {"message": "comment %d" % i},
                 "member": {"uname": "A"}} for i in range(1, 4)]
    page = {"code": 0, "data": {"replies": comments}}
    client = _Client(_view(), {"code": 0, "data": {}}, reply_pages={1: page})

    got = _run(monkeypatch, client, with_comments=True, comment_limit=3)

    coverage = got.as_patch()["discussion_coverage"]
    assert coverage["fetched_comments"] == 3
    assert coverage["status"] == "partial"
    assert "comment_limit" in coverage["truncated_by"]


def test_full_last_page_without_total_count_reports_page_cap(monkeypatch):
    pages = {}
    for page_number in (1, 2, 3):
        start = (page_number - 1) * 20 + 1
        rows = [{"rpid": i, "content": {"message": "comment %d" % i},
                 "member": {"uname": "A"}} for i in range(start, start + 20)]
        pages[page_number] = {"code": 0, "data": {"replies": rows}}
    client = _Client(_view(), {"code": 0, "data": {}}, reply_pages=pages)

    got = _run(monkeypatch, client, with_comments=True, comment_limit=100)

    coverage = got.as_patch()["discussion_coverage"]
    assert coverage["fetched_roots"] == 60
    assert coverage["status"] == "partial"
    assert "comment_page_limit" in coverage["truncated_by"]


def test_comments_fetch_missing_subreplies_and_preserve_parent_ids(monkeypatch):
    root = {"rpid": 11, "rcount": 3, "content": {"message": "root"},
            "member": {"uname": "A"}, "replies": [
                {"rpid": 12, "parent": 11, "content": {"message": "inline"},
                 "member": {"uname": "B"}}]}
    first = {"code": 0, "data": {"page": {"count": 1}, "replies": [root]}}
    children = {"code": 0, "data": {"page": {"count": 3}, "replies": [
        {"rpid": 12, "parent": 11, "content": {"message": "inline"},
         "member": {"uname": "B"}},
        {"rpid": 13, "parent": 11, "content": {"message": "deep one"},
         "member": {"uname": "C"}},
        {"rpid": 14, "parent": 11, "content": {"message": "deep two"},
         "member": {"uname": "D"}}]}}
    client = _Client(_view(), {"code": 0, "data": {}}, reply_pages={1: first},
                     subreply={11: children})

    got = _run(monkeypatch, client, with_comments=True, comment_limit=4)

    assert [reply["text"] for reply in got.comments[0]["replies"]] == [
        "inline", "deep one", "deep two"]
    assert all(reply["parent_id"] == 11 for reply in got.comments[0]["replies"])
    assert got.as_patch()["discussion_coverage"]["fetched_comments"] == 4
    assert any("v2/reply/reply" in call[0] for call in client.calls)


def test_comment_fetch_error_is_not_silently_reported_as_empty(monkeypatch):
    class ReplyDown(_Client):
        async def get(self, url, params=None, **kw):
            if "v2/reply" in url:
                raise httpx.ConnectError("offline")
            return await super().get(url, params=params, **kw)

    got = _run(monkeypatch, ReplyDown(_view(), {"code": 0, "data": {}}),
                with_comments=True)

    coverage = got.as_patch()["discussion_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["errors"] == ["comments:ConnectError"]


def test_enrich_many_with_zero_budget_skips_network_and_reports_partial(monkeypatch):
    client = _Client(_view(), {"code": 0, "data": {}})
    monkeypatch.setattr(be.httpx, "AsyncClient", lambda *a, **k: client)

    got = asyncio.run(be.enrich_many(["BV1xxxxxxxxx"], with_comments=True,
                                     budget_seconds=0))

    assert client.calls == []
    coverage = got["BV1xxxxxxxxx"].as_patch()["discussion_coverage"]
    assert coverage["status"] == "partial"
    assert "time_budget" in coverage["truncated_by"]


def test_patch_contains_only_what_was_measured(monkeypatch):
    client = _Client(_view(), {"code": 0, "data": {"subtitle": {"subtitles": []}}})
    patch = _run(monkeypatch, client).as_patch()
    assert "metrics" in patch
    assert "transcript" not in patch, "no transcript means no transcript key"
    assert patch["ids"]["cid"] == 42


def test_enrich_many_isolates_a_failure(monkeypatch):
    async def fake_enrich(bvid, **kw):
        if bvid == "BVBAD":
            raise RuntimeError("boom")
        return be.Enriched(bvid=bvid, metrics={"play": 1})

    monkeypatch.setattr(be, "enrich", fake_enrich)
    got = asyncio.run(be.enrich_many(["BVA", "BVBAD", "BVC"]))
    assert got["BVA"].metrics == {"play": 1}
    assert got["BVC"].metrics == {"play": 1}
    assert got["BVBAD"].errors[0].startswith("enrich:"), (
        "one failing video must not empty the batch")
