import asyncio

import httpx

from src.corpus.store import Corpus


def test_hn_investigation_persists_story_and_nested_replies(monkeypatch, scratch_path):
    from periscope.collection.crawling.engine import CrawlOptions
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions

    async def scenario():
        corpus = Corpus(scratch_path / "hn.db")
        payloads = {
            "10": {"id": 10, "type": "story", "title": "Robot research", "text": "<p>Original story</p>", "by": "author", "kids": [11], "descendants": 2},
            "11": {"id": 11, "type": "comment", "text": "first comment", "by": "reader", "kids": [12]},
            "12": {"id": 12, "type": "comment", "text": "nested reply", "by": "another"},
        }
        calls = []
        async def handler(request):
            identifier = request.url.path.rsplit("/", 1)[-1].split(".")[0]
            calls.append(identifier)
            return httpx.Response(200, json=payloads[identifier])
        client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler)))
        options = CrawlOptions(("https://news.ycombinator.com/item?id=10",), max_pages=4, depth=2, budget=30)
        runner = InvestigationRunner(corpus, lambda message: stream_crawl(message, options))
        rid = runner.submit(runner.store.create("robot"), "robot", "one")
        await runner.wait(rid)
        assert runner.store.get_round(rid)["status"] == "completed"
        refs = runner.store.references(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in refs]
        assert next(item for item in snapshots if item.get("type") == "story")["text"] == "Original story"
        reply = next(item for item in snapshots if item.get("native_id") == "12")
        assert reply["text"] == "nested reply" and reply["parent_native_id"] == "11"
        assert corpus.search("nested reply") and not corpus.search("nested reply", tier="claimable")
        assert sorted(calls) == ["10", "11", "12"]
        # A no-comments observation still updates coverage in the searchable corpus.
        from periscope.operations.ingest import _hn_discussion_version
        incomplete = {"title": "robot", "discussion_coverage": {"status": "not_attempted"}}
        complete = {"title": "robot", "discussion_coverage": {"status": "complete"}}
        assert _hn_discussion_version(incomplete) != _hn_discussion_version(complete)
        assert _hn_discussion_version({**complete, "topic_detail": {"external_url": "https://example.org"}}) != _hn_discussion_version(complete)
        corpus.close()
    asyncio.run(scenario())


def test_default_discovery_continues_when_one_source_fails(monkeypatch):
    from periscope.investigations import crawl

    async def v2ex(*args, **kwargs):
        raise crawl.CollectionIncomplete("v2ex:search_unavailable")
        yield []
    async def hn(*args, **kwargs):
        yield [{"source": "hn_ai", "text": "hn result"}]
    monkeypatch.setattr(crawl, "stream_v2ex_search", v2ex)
    monkeypatch.setattr(crawl, "stream_hn_search", hn)

    async def scenario():
        batches = []
        try:
            async for batch in crawl.stream_auto_search("robot", budget=30, max_pages=6, depth=2):
                batches.extend(batch)
        except crawl.CollectionIncomplete as exc:
            assert "v2ex:search_unavailable" in str(exc)
        else:
            raise AssertionError("partial coverage must be reported")
        assert [item["text"] for item in batches] == ["hn result"]
    asyncio.run(scenario())


def test_hn_search_saves_candidates_then_follows_discussion(monkeypatch):
    from periscope.investigations import crawl
    from periscope.collection.strategies import focused
    async def discover(*args, **kwargs):
        assert kwargs["sources"] == ["hn_ai"]
        return ([{"ids": {"objectID": "10"}, "title": "robot", "text": "search text",
                  "url": "https://example.org/article", "date": "2026-01-01T00:00:00Z"}], {"failed": [], "over_budget": []})
    async def detail(story_id, options):
        assert story_id == "10" and options.max_pages == 3
        yield [{"source": "hn_ai", "text": "full"}]
    monkeypatch.setattr(focused, "collect_topic_async", discover)
    monkeypatch.setattr(crawl, "stream_hn_story", detail)
    async def scenario():
        rows = []
        async for batch in crawl.stream_hn_search("robot", budget=30, max_pages=4, depth=2):
            rows.extend(batch)
        assert rows[0]["topic_detail"]["content_level"] == "search_result"
        assert rows[0]["topic_detail"]["external_url"] == "https://example.org/article"
        assert rows[1]["text"] == "full"
    asyncio.run(scenario())


def test_cancelling_concurrent_discovery_cleans_up_both_sources(monkeypatch):
    from periscope.investigations import crawl
    async def scenario():
        started, finished = [], []
        async def source(topic, *, budget, max_pages, depth):
            started.append(max_pages)
            try:
                yield [{"text": "found"}]
                await asyncio.Future()
            finally:
                finished.append(max_pages)
        monkeypatch.setattr(crawl, "stream_v2ex_search", source)
        monkeypatch.setattr(crawl, "stream_hn_search", source)
        stream = crawl.stream_auto_search("robot", budget=30, max_pages=7, depth=2)
        await anext(stream)
        await asyncio.wait_for(stream.aclose(), timeout=2)
        assert sorted(started) == [3, 4] and sorted(finished) == [3, 4]
    asyncio.run(scenario())


def test_hn_search_retains_independent_discussions_on_same_link():
    import dataclasses
    from periscope.collection.registry import SourceRegistry
    from periscope.collection.transport.client import collect
    source = dataclasses.replace(SourceRegistry().get("hn_ai").source, queries=("robot",), max_pages=1)
    async def scenario():
        async def handler(request):
            return httpx.Response(200, json={"hits": [
                {"objectID": "1", "title": "same", "url": "https://example.org/article"},
                {"objectID": "2", "title": "same", "url": "https://example.org/article"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rows, stats = await collect(client, source)
        assert not stats.error
        assert {row["ids"]["objectID"] for row in rows} == {"1", "2"}
    asyncio.run(scenario())


def test_hn_search_limits_cards_and_reports_truncation(monkeypatch):
    from periscope.investigations import crawl
    from periscope.collection.strategies import focused
    async def discover(*args, **kwargs):
        return ([{"ids": {"objectID": str(i)}, "title": "robot", "url": "https://example.org"} for i in range(1, 32)],
                {"failed": [], "over_budget": []})
    monkeypatch.setattr(focused, "collect_topic_async", discover)
    async def scenario():
        rows = []
        try:
            async for batch in crawl.stream_hn_search("robot", budget=30, max_pages=1, depth=0):
                rows.extend(batch)
        except crawl.CollectionIncomplete as exc:
            assert "search_result_limit" in str(exc)
        assert len(rows) == 20
    asyncio.run(scenario())


def test_cancelling_hn_discussion_cancels_child_requests():
    from periscope.collection.platforms.hackernews import fetch_discussion
    async def scenario():
        started, completed, ready = [], [], asyncio.Event()
        async def handler(request):
            identifier = request.url.path
            started.append(identifier)
            if len(started) == 2:
                ready.set()
            try:
                await asyncio.Future()
            finally:
                completed.append(identifier)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(fetch_discussion("10", client=client, story={"id": 10, "kids": [11, 12], "descendants": 2}))
            await asyncio.wait_for(ready.wait(), 2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert sorted(started) == sorted(completed)
    asyncio.run(scenario())
