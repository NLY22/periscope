"""HN public discussion traversal must stay bounded and preserve its tree."""

from __future__ import annotations

import asyncio
import json
import time

import httpx


def test_hn_story_search_keeps_native_id_and_discussion_permalink():
    from periscope.collection.registry import SourceRegistry
    from periscope.collection.strategies.broad import _linked_row
    from periscope.collection.transport.parsing import extract_rows

    entry = SourceRegistry().get("hn_ai")
    body = json.dumps({"hits": [{
        "objectID": "4242", "title": "A research discussion",
        "url": "https://example.org/paper", "story_text": "context",
        "author": "reader", "created_at": "2026-10-24T00:00:00Z",
    }]})

    row = extract_rows(entry.source, body, "application/json")[0]
    linked = _linked_row(entry, row, "2026-10-24T00:00:00+00:00")

    assert row["ids"] == {"objectID": "4242"}
    assert linked["url"] == "https://example.org/paper"
    assert linked["body_source_url"] == "https://example.org/paper"
    assert linked["discussion_url"] == "https://news.ycombinator.com/item?id=4242"


def test_fetch_discussion_preserves_firebase_order_and_reply_parentage():
    from periscope.collection.platforms.hackernews import fetch_discussion

    items = {
        "100": {"id": 100, "type": "story", "descendants": 3, "kids": [101, 102]},
        "101": {"id": 101, "type": "comment", "by": "Ada", "time": 1700000000,
                "text": "<p>first &amp; foremost</p>", "parent": 100, "kids": [103]},
        "102": {"id": 102, "type": "comment", "by": "Lin", "text": "second", "parent": 100},
        "103": {"id": 103, "type": "comment", "by": "Sam", "text": "nested", "parent": 101},
    }
    calls = []

    async def route(request):
        item_id = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        calls.append(item_id)
        return httpx.Response(200, json=items[item_id])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("100", client=client)

    comments, coverage = asyncio.run(run())

    assert [node["id"] for node in comments] == ["101", "102"]
    assert comments[0]["text"] == "first & foremost"
    assert comments[0]["url"] == "https://news.ycombinator.com/item?id=101"
    assert comments[0]["replies"][0]["id"] == "103"
    assert comments[0]["replies"][0]["parent_id"] == "101"
    assert coverage["status"] == "complete"
    assert coverage["attempted_ids"] == 3
    assert coverage["max_depth_seen"] == 2
    assert sorted(calls) == ["100", "101", "102", "103"]


def test_fetch_discussion_marks_comment_cap_and_keeps_partial_nodes():
    from periscope.collection.platforms.hackernews import fetch_discussion

    items = {
        "200": {"id": 200, "type": "story", "descendants": 3, "kids": [201, 202, 203]},
        "201": {"id": 201, "type": "comment", "by": "A", "text": "one", "parent": 200},
        "202": {"id": 202, "type": "comment", "by": "B", "text": "two", "parent": 200},
        "203": {"id": 203, "type": "comment", "by": "C", "text": "three", "parent": 200},
    }

    async def route(request):
        item_id = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        return httpx.Response(200, json=items[item_id])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("200", max_comments=2, client=client)

    comments, coverage = asyncio.run(run())

    assert [node["id"] for node in comments] == ["201", "202"]
    assert coverage["status"] == "partial"
    assert coverage["truncated_by"] == ["comment_limit"]
    assert coverage["attempted_ids"] == 2


def test_fetch_discussion_marks_depth_cap_without_fetching_deeper_nodes():
    from periscope.collection.platforms.hackernews import fetch_discussion

    items = {
        "250": {"id": 250, "type": "story", "descendants": 2, "kids": [251]},
        "251": {"id": 251, "type": "comment", "text": "parent", "kids": [252]},
        "252": {"id": 252, "type": "comment", "text": "child", "parent": 251},
    }
    calls = []

    async def route(request):
        item_id = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        calls.append(item_id)
        return httpx.Response(200, json=items[item_id])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("250", max_depth=1, client=client)

    comments, coverage = asyncio.run(run())

    assert [node["id"] for node in comments] == ["251"]
    assert "replies" not in comments[0]
    assert coverage["truncated_by"] == ["depth_limit"]
    assert calls == ["250", "251"]


def test_fetch_discussion_deduplicates_cyclic_child_ids():
    from periscope.collection.platforms.hackernews import fetch_discussion

    items = {
        "260": {"id": 260, "type": "story", "descendants": 2, "kids": [261, 261]},
        "261": {"id": 261, "type": "comment", "text": "one", "kids": [262, 261]},
        "262": {"id": 262, "type": "comment", "text": "two", "parent": 261, "kids": [261]},
    }
    calls = []

    async def route(request):
        item_id = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        calls.append(item_id)
        return httpx.Response(200, json=items[item_id])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("260", client=client)

    comments, coverage = asyncio.run(run())

    assert [node["id"] for node in comments] == ["261"]
    assert [node["id"] for node in comments[0]["replies"]] == ["262"]
    assert calls.count("261") == 1
    assert calls.count("262") == 1
    assert coverage["status"] == "complete"


def test_fetch_discussion_keeps_fast_nodes_when_later_batch_times_out():
    from periscope.collection.platforms.hackernews import fetch_discussion

    items = {
        "270": {"id": 270, "type": "story", "descendants": 2, "kids": [271, 272]},
        "271": {"id": 271, "type": "comment", "text": "already fetched"},
        "272": {"id": 272, "type": "comment", "text": "too slow"},
    }

    async def route(request):
        item_id = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        if item_id == "272":
            await asyncio.sleep(0.2)
        return httpx.Response(200, json=items[item_id])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("270", budget_seconds=0.05, client=client)

    comments, coverage = asyncio.run(run())

    assert [node["text"] for node in comments] == ["already fetched"]
    assert coverage["status"] == "partial"
    assert coverage["truncated_by"] == ["time_budget"]
    assert coverage["fetched_comments"] == 1


def test_fetch_discussion_continues_through_deleted_parent_and_keeps_other_nodes():
    from periscope.collection.platforms.hackernews import fetch_discussion

    items = {
        "280": {"id": 280, "type": "story", "descendants": 3, "kids": [281, 282]},
        "281": {"id": 281, "type": "comment", "deleted": True, "kids": [283]},
        "283": {"id": 283, "type": "comment", "text": "survivor", "parent": 281},
    }

    async def route(request):
        item_id = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        if item_id == "282":
            return httpx.Response(404, json={"error": "missing"})
        return httpx.Response(200, json=items[item_id])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("280", client=client)

    comments, coverage = asyncio.run(run())

    assert [node["id"] for node in comments] == ["283"]
    assert comments[0]["parent_id"] == "281"
    assert comments[0]["text"] == "survivor"
    assert coverage["status"] == "partial"
    assert any(error.startswith("item:282:") for error in coverage["errors"])


def test_fetch_discussion_rejects_invalid_story_id_without_network():
    from periscope.collection.platforms.hackernews import fetch_discussion

    comments, coverage = asyncio.run(fetch_discussion("not-an-id"))

    assert comments == []
    assert coverage["status"] == "failed"
    assert coverage["errors"] == ["invalid_story_id"]


def test_story_request_obeys_aggregate_budget():
    from periscope.collection.platforms.hackernews import fetch_discussion

    async def route(_request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"id": 300, "type": "story", "descendants": 0, "kids": []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("300", budget_seconds=0.02, client=client)

    started = time.monotonic()
    comments, coverage = asyncio.run(run())
    elapsed = time.monotonic() - started

    assert comments == []
    assert elapsed < 0.12
    assert coverage["status"] == "partial"
    assert coverage["truncated_by"] == ["time_budget"]


def test_topic_service_routes_hn_rows_through_enrichment_and_keeps_raw_patch(monkeypatch):
    from periscope.collection.platforms import hackernews
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService

    observed = {}

    async def enrich_many(story_ids, *, concurrency, budget_seconds):
        observed.update(story_ids=story_ids, concurrency=concurrency,
                        budget_seconds=budget_seconds)
        return {"4242": ([{"id": "5001", "user": "Reader", "text": "threadneedle",
                           "parent_id": "4242", "url": "https://news.ycombinator.com/item?id=5001"}],
                          {"status": "partial", "attempted_ids": 1,
                           "reported_descendants": 12, "max_depth_seen": 1,
                           "max_depth": 4, "truncated_by": ["comment_limit"],
                           "errors": []})}

    monkeypatch.setattr(hackernews, "enrich_many", enrich_many)
    story = {"title": "Research thread", "url": "https://example.org/paper",
             "discussion_url": "https://news.ycombinator.com/item?id=4242",
             "source": "hn_ai", "kind": "community", "ids": {"objectID": "4242"},
             "text": "context", "_origin": "platform"}
    service = TopicService(
        local_lookup=lambda *_: ([], 0),
        api_lookup=lambda *_: ([], 0.0, ""),
        platform_lookup=lambda *_args, **_kwargs: ([story], {"elapsed": 0.01, "failed": [],
                                                            "source_reports": []}),
        api_available=lambda: False,
    )

    result = service.search(TopicRequest(topic="research", no_local=True, no_api=True,
                                         speed="normal", enrich=True, budget=7))

    assert observed == {"story_ids": ["4242"], "concurrency": 4, "budget_seconds": 7}
    assert result.platform_rows[0]["comments"][0]["text"] == "threadneedle"
    assert result.raw_rows[0]["discussion_coverage"]["status"] == "partial"
    assert any("HN 讨论树 1/1" in part for part in result.breakdown.split(" · "))
    assert any("达到深度" in note for note in result.notes), result.notes
