"""V2EX topic enrichment must keep original text and bounded public replies."""

from __future__ import annotations

import asyncio

import httpx


def test_v2ex_topic_and_reply_api_are_normalized_with_topic_relationships():
    from periscope.collection.platforms.v2ex import fetch_discussion

    calls = []

    async def route(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/topics/show.json"):
            return httpx.Response(200, json=[{
                "id": 85402, "title": "API thread", "url": "https://www.v2ex.com/t/85402",
                "content": "Full topic body", "created": 1700000000,
                "replies": 2, "member": {"username": "author"},
                "node": {"name": "v2exapi"},
            }])
        return httpx.Response(200, json=[
            {"id": 11, "topic_id": 85402, "content": "first reply", "created": 1700000010,
             "member": {"username": "reader-a"}},
            {"id": 12, "topic_id": 85402, "content": "second reply", "created": 1700000020,
             "member": {"username": "reader-b"}},
        ])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("85402", client=client)

    detail, comments, coverage = asyncio.run(run())

    assert detail["text"] == "Full topic body"
    assert detail["author"] == "author"
    assert [comment["text"] for comment in comments] == ["first reply", "second reply"]
    assert comments[0]["parent_id"] == "85402"
    assert comments[0]["url"] == "https://www.v2ex.com/t/85402#11"
    assert coverage["status"] == "complete"
    assert coverage["reported_replies"] == 2
    assert coverage["fetched_replies"] == 2
    assert {path for path, _params in calls} == {
        "/api/topics/show.json", "/api/replies/show.json"}


def test_v2ex_reply_limit_keeps_partial_material_and_reports_coverage():
    from periscope.collection.platforms.v2ex import fetch_discussion

    async def route(request):
        if request.url.path.endswith("/topics/show.json"):
            return httpx.Response(200, json=[{"id": 85403, "replies": 4,
                                              "content": "Body", "member": {}}])
        return httpx.Response(200, json=[
            {"id": index, "topic_id": 85403, "content": "reply %d" % index,
             "member": {"username": "reader"}} for index in range(1, 5)])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("85403", max_replies=2, client=client)

    detail, comments, coverage = asyncio.run(run())

    assert detail["text"] == "Body"
    assert len(comments) == 2
    assert coverage["status"] == "partial"
    assert coverage["truncated_by"] == ["comment_limit"]


def test_v2ex_keeps_topic_when_reply_request_exceeds_time_budget():
    from periscope.collection.platforms.v2ex import fetch_discussion

    async def route(request):
        if request.url.path.endswith("/topics/show.json"):
            return httpx.Response(200, json=[{"id": 85404, "content": "Saved body",
                                              "member": {"username": "author"}}])
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=[])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("85404", budget_seconds=0.05, client=client)

    detail, comments, coverage = asyncio.run(run())

    assert detail["text"] == "Saved body"
    assert comments == []
    assert coverage["status"] == "partial"
    assert "time_budget" in coverage["truncated_by"]


def test_topic_service_routes_v2ex_search_hit_and_patches_raw_run(monkeypatch):
    from periscope.collection.platforms import v2ex
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService

    observed = {}

    async def fetch_discussion(topic_id, *, max_replies=60, budget_seconds=None):
        observed.update(topic_id=topic_id, max_replies=max_replies,
                        budget_seconds=budget_seconds)
        return ({"text": "Full post body", "author": "writer", "node": "python",
                 "topic_id": topic_id, "reported_replies": 1},
                [{"id": "99", "user": "reader", "text": "Discussion text",
                  "parent_id": topic_id, "url": "https://www.v2ex.com/t/85405#99",
                  "discussion_type": "reply"}],
                {"status": "complete", "fetched_replies": 1,
                 "reported_replies": 1, "truncated_by": [], "errors": []})

    monkeypatch.setattr(v2ex, "fetch_discussion", fetch_discussion)
    topic = {"title": "Search hit", "url": "https://www.v2ex.com/t/85405",
             "source": "v2ex_search", "kind": "community",
             "ids": {"_source.id": "https://www.v2ex.com/t/85405"},
             "text": "Search snippet", "_origin": "platform"}
    service = TopicService(
        local_lookup=lambda *_: ([], 0),
        api_lookup=lambda *_: ([], 0.0, ""),
        platform_lookup=lambda *_args, **_kwargs: ([topic], {"elapsed": 0.0,
                                                             "failed": [], "source_reports": []}),
        api_available=lambda: False,
    )

    result = service.search(TopicRequest(topic="python", no_local=True, no_api=True,
                                         speed="normal", enrich=True, budget=7))

    assert observed == {"topic_id": "85405", "max_replies": 60, "budget_seconds": 7}
    assert result.platform_rows[0]["text"] == "Full post body"
    assert result.platform_rows[0]["comments"][0]["text"] == "Discussion text"
    assert result.raw_rows[0]["discussion_coverage"]["status"] == "complete"


def test_topic_service_passes_only_unspent_collection_budget_to_enrichment(monkeypatch):
    import periscope.topics.service as topic_service
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService

    observed = {}

    def enrich(rows, *, k, budget_seconds):
        observed.update(k=k, budget_seconds=budget_seconds)
        return rows, {"targets": 0, "done": 0, "elapsed": 0.0, "failed": [],
                      "no_transcript": 0}, {}

    monkeypatch.setattr(topic_service, "_enrich_topk", enrich)
    service = TopicService(
        local_lookup=lambda *_: ([], 0),
        api_lookup=lambda *_: ([], 0.0, ""),
        platform_lookup=lambda *_args, **_kwargs: ([], {"elapsed": 4.25,
                                                        "failed": [], "source_reports": []}),
        api_available=lambda: False,
    )

    service.search(TopicRequest(topic="budget", no_local=True, no_api=True,
                                speed="normal", enrich=True, budget=10))

    assert observed["budget_seconds"] == 5.75
