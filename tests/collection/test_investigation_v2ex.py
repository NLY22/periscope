import asyncio

import httpx
import pytest
from src.corpus.store import Corpus


def test_v2ex_round_saves_post_and_reply_versions(monkeypatch, scratch_path):
    from periscope.collection.crawling.engine import CrawlOptions
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions

    async def scenario():
        corpus = Corpus(scratch_path / "v2ex.db")
        reply_text = ["robot unique reply"]
        async def handler(request):
            if request.url.path == "/api/topics/show.json":
                assert request.url.params["id"] == "123"
                return httpx.Response(200, json=[{"id": 123, "title": "robot problem", "content": "robot post",
                                                  "member": {"username": "author"}, "replies": 1}])
            assert request.url.path == "/api/replies/show.json"
            assert request.url.params["topic_id"] == "123"
            return httpx.Response(200, json=[{"id": 456, "content": reply_text[0], "member": {"username": "reader"}}])
        client_type = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
        options = CrawlOptions(("https://www.v2ex.com/t/123",), max_pages=2, depth=1, budget=30)
        runner = InvestigationRunner(corpus, lambda message: stream_crawl(message, options))
        session = runner.store.create("robot")
        round_id = runner.submit(session, "robot", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "completed"
        refs = runner.store.references(round_id)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in refs]
        reply = next(item for item in snapshots if item.get("type") == "reply")
        assert reply["text"] == "robot unique reply" and reply["parent_native_id"] == "123"
        assert corpus.search("robot unique reply")
        assert not corpus.search("robot unique reply", tier="claimable")
        old_reply = next(ref for ref in refs if EvidenceVersions(corpus).get_version(**ref).get("type") == "reply")
        assert EvidenceVersions(corpus).observations(old_reply["object_id"])[0]["provenance"]["discussion_coverage"]["status"] == "complete"
        reply_text[0] = "robot updated reply"
        second = runner.submit(session, "robot", "second")
        await runner.wait(second)
        updated = next(ref for ref in runner.store.references(second)
                       if EvidenceVersions(corpus).get_version(**ref).get("type") == "reply")
        assert old_reply["object_id"] == updated["object_id"] and old_reply["version_id"] != updated["version_id"]
        assert corpus.search("robot unique reply") and corpus.search("robot updated reply")
        corpus.close()
    asyncio.run(scenario())


def test_cancelling_v2ex_collection_stops_its_network_requests():
    from periscope.collection.platforms.v2ex import fetch_discussion

    async def scenario():
        started = asyncio.Event()
        requests = []
        finished = []
        async def handler(request):
            requests.append(request.url.path)
            if len(requests) == 2:
                started.set()
            try:
                await asyncio.Future()
            finally:
                finished.append(request.url.path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(fetch_discussion("123", client=client))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            assert sorted(finished) == sorted(requests)
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["title_only", "missing_topic", "rate_limited", "invalid_replies"])
def test_v2ex_partial_and_empty_body_cases_are_honest(monkeypatch, scratch_path, case):
    from periscope.collection.crawling.engine import CrawlOptions
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions

    async def scenario():
        corpus = Corpus(scratch_path / "cases.db")
        async def handler(request):
            if case == "rate_limited":
                return httpx.Response(429)
            if request.url.path == "/api/topics/show.json":
                if case == "missing_topic":
                    return httpx.Response(503)
                return httpx.Response(200, json=[{"id": 123, "title": "robot title", "content": "",
                                                  "member": {"username": "author"}, "replies": 0}])
            if case == "missing_topic":
                return httpx.Response(200, json=[{"id": 456, "content": "robot reply", "member": {"username": "reader"}}])
            return httpx.Response(200, json={} if case == "invalid_replies" else [])

        client_type = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
        options = CrawlOptions(("https://www.v2ex.com/t/123",), max_pages=2, depth=1, budget=30)
        runner = InvestigationRunner(corpus, lambda message: stream_crawl(message, options))
        session = runner.store.create("robot")
        round_id = runner.submit(session, "robot", "key")
        await runner.wait(round_id)
        state = runner.store.get_round(round_id)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(round_id)]
        if case == "title_only":
            assert state["status"] == "completed" and any(item.get("type") == "topic" for item in snapshots)
        elif case == "missing_topic":
            assert state["status"] == "partial" and [item["type"] for item in snapshots] == ["reply"]
        elif case == "rate_limited":
            assert state["status"] == "partial" and "http_429" in state["error"]
        else:
            assert state["status"] == "partial" and "invalid_payload" in state["error"]
        corpus.close()
    asyncio.run(scenario())
