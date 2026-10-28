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
            assert "invalid_payload" not in state["error"]
        else:
            assert state["status"] == "partial" and "invalid_payload" in state["error"]
        corpus.close()
    asyncio.run(scenario())


def test_topic_discovers_v2ex_candidate_then_fetches_replies(monkeypatch, scratch_path):
    from periscope.collection.platforms import v2ex
    from periscope.collection.strategies import focused
    from periscope.investigations.crawl import stream_v2ex_search
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions

    async def scenario():
        corpus = Corpus(scratch_path / "discovery.db")
        async def search(topic, **kwargs):
            assert topic == "robot" and kwargs["sources"] == ["v2ex_search"]
            return [{"title": "robot question", "url": "https://www.v2ex.com/t/123",
                     "text": "robot search snippet", "author": "author", "date": "2026-10-01"}], {"failed": [], "over_budget": []}
        async def fetch(topic_id, **kwargs):
            assert corpus.search("robot search snippet")
            return ({"topic_id": "123", "title": "robot question", "text": "robot full post",
                     "url": "https://www.v2ex.com/t/123", "author": "author"},
                    [{"id": "456", "text": "robot reply", "parent_id": "123", "user": "reader"}],
                    {"status": "complete", "collected_at": "2026-10-28", "errors": [], "truncated_by": []})
        monkeypatch.setattr(focused, "collect_topic_async", search)
        monkeypatch.setattr(v2ex, "fetch_discussion", fetch)
        runner = InvestigationRunner(corpus, lambda message: stream_v2ex_search(message, budget=120))
        session = runner.store.create("robot")
        round_id = runner.submit(session, "robot", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "completed"
        versions = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(round_id)]
        assert any(row.get("content_level") == "search_result" for row in versions)
        assert next(row for row in versions if row.get("content_level") == "search_result")["published_at"] == "2026-10-01"
        assert any(row.get("text") == "robot full post" for row in versions)
        assert any(row.get("type") == "reply" for row in versions)
        corpus.close()
    asyncio.run(scenario())


def test_cancelling_discovery_stops_its_source_task(monkeypatch):
    from periscope.collection.strategies import focused

    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def one(*args):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        class Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        monkeypatch.setattr(focused, "_one", one)
        monkeypatch.setattr(focused.httpx, "AsyncClient", lambda **kwargs: Client())
        task = asyncio.create_task(focused.collect_topic_async("robot", sources=["v2ex_search"], budget=30))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
    asyncio.run(scenario())


def test_discovery_keeps_all_search_cards_when_followups_are_limited(monkeypatch, scratch_path):
    from periscope.collection.platforms import v2ex
    from periscope.collection.strategies import focused
    from periscope.investigations.crawl import stream_v2ex_search
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions

    async def scenario():
        corpus = Corpus(scratch_path / "candidates.db")
        async def search(topic, **kwargs):
            return ([{"title": f"robot {i}", "url": f"https://www.v2ex.com/t/{i}",
                      "text": f"robot candidate {i}"} for i in range(1, 5)],
                    {"failed": [], "over_budget": []})
        async def fetch(topic_id, **kwargs):
            return ({"topic_id": topic_id, "title": "robot", "text": "robot details"}, [],
                    {"status": "complete", "errors": [], "truncated_by": []})
        monkeypatch.setattr(focused, "collect_topic_async", search)
        monkeypatch.setattr(v2ex, "fetch_discussion", fetch)
        runner = InvestigationRunner(corpus, lambda message: stream_v2ex_search(message, budget=120))
        session = runner.store.create("robot")
        round_id = runner.submit(session, "robot", "key")
        await runner.wait(round_id)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(round_id)]
        assert any(item.get("text") == "robot candidate 4" for item in snapshots)
        assert runner.store.get_round(round_id)["status"] == "partial"
        assert "candidate_limit" in runner.store.get_round(round_id)["error"]
        corpus.close()
    asyncio.run(scenario())


def test_discovery_reports_rate_limit_instead_of_empty_result(monkeypatch, scratch_path):
    from periscope.collection.strategies import focused
    from periscope.investigations.crawl import stream_v2ex_search
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "rate.db")
        async def search(topic, **kwargs):
            return [], {"failed": ["v2ex_search"], "over_budget": [],
                        "per": [("v2ex_search", 0, 0.1, "HTTP 429 (throttled)")]}
        monkeypatch.setattr(focused, "collect_topic_async", search)
        runner = InvestigationRunner(corpus, lambda message: stream_v2ex_search(message, budget=120))
        session = runner.store.create("robot")
        round_id = runner.submit(session, "robot", "key")
        await runner.wait(round_id)
        state = runner.store.get_round(round_id)
        assert state["status"] == "partial" and "429" in state["error"]
        corpus.close()
    asyncio.run(scenario())


def test_real_search_parser_discovers_and_follows_v2ex_discussion(monkeypatch, scratch_path):
    from periscope.investigations.crawl import stream_v2ex_search
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions

    async def scenario():
        corpus = Corpus(scratch_path / "integration.db")
        async def handler(request):
            if request.url.host == "www.sov2ex.com":
                assert request.url.params["q"] == "robot"
                return httpx.Response(200, json={"hits": [{"_source": {"id": 123, "title": "robot question",
                                                "content": "robot search snippet", "member": "author",
                                                "created": "2026-10-01T12:34:56.123"}}]})
            if request.url.path == "/api/topics/show.json":
                return httpx.Response(200, json=[{"id": 123, "title": "robot question", "content": "robot full post",
                                                  "member": {"username": "author"}, "replies": 1}])
            assert request.url.path == "/api/replies/show.json"
            return httpx.Response(200, json=[{"id": 456, "content": "robot reply evidence", "member": {"username": "reader"}}])
        client_type = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
        runner = InvestigationRunner(corpus, lambda message: stream_v2ex_search(message, budget=120))
        session = runner.store.create("robot")
        round_id = runner.submit(session, "robot", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "completed"
        versions = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(round_id)]
        assert any(row.get("content_level") == "search_result" for row in versions)
        assert any(row.get("text") == "robot reply evidence" for row in versions)
        corpus.close()
    asyncio.run(scenario())


def test_search_card_and_equal_full_text_remain_distinct_in_corpus(scratch_path):
    from periscope.evidence.journal import save_batch

    corpus = Corpus(scratch_path / "same.db")
    row = {"source": "v2ex_search", "kind": "community", "title": "robot", "text": "robot short",
           "url": "https://www.v2ex.com/t/123", "author": "author",
           "topic_detail": {"topic_id": "123", "content_level": "search_result"},
           "discussion_coverage": {"status": "not_attempted"}}
    save_batch(corpus, [row])
    save_batch(corpus, [{**row, "topic_detail": {"topic_id": "123", "content_level": "full"},
                         "discussion_coverage": {"status": "complete"}}])
    assert len(corpus.search("robot short")) == 2
    corpus.close()


def test_discovery_respects_page_and_depth_limits(monkeypatch, scratch_path):
    from periscope.collection.platforms import v2ex
    from periscope.collection.strategies import focused
    from periscope.investigations.crawl import stream_v2ex_search
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "limits.db")
        async def search(topic, **kwargs):
            return [{"title": "robot", "url": "https://www.v2ex.com/t/123", "text": "robot snippet"}], {"failed": [], "over_budget": []}
        async def fetch(*args, **kwargs):
            raise AssertionError("depth zero must not fetch topic details")
        monkeypatch.setattr(focused, "collect_topic_async", search)
        monkeypatch.setattr(v2ex, "fetch_discussion", fetch)
        runner = InvestigationRunner(corpus, lambda message: stream_v2ex_search(message, budget=120, max_pages=1, depth=0))
        investigation = runner.store.create("robot")
        round_id = runner.submit(investigation, "robot", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "partial"
        assert corpus.search("robot snippet")
        assert "depth_limit" in runner.store.get_round(round_id)["error"]
        corpus.close()
    asyncio.run(scenario())


def test_search_and_detail_count_as_two_requests(monkeypatch, scratch_path):
    from periscope.collection.platforms import v2ex
    from periscope.collection.strategies import focused
    from periscope.investigations.crawl import stream_v2ex_search
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "request-budget.db")
        async def search(topic, **kwargs):
            return [{"title": "robot", "url": "https://www.v2ex.com/t/123", "text": "robot snippet"}], {"failed": [], "over_budget": []}
        async def fetch(topic_id, **kwargs):
            assert kwargs["max_replies"] == 0
            return ({"topic_id": topic_id, "title": "robot", "text": "robot detail"}, [],
                    {"status": "partial", "errors": [], "truncated_by": ["comment_limit"]})
        monkeypatch.setattr(focused, "collect_topic_async", search)
        monkeypatch.setattr(v2ex, "fetch_discussion", fetch)
        runner = InvestigationRunner(corpus, lambda message: stream_v2ex_search(message, budget=120, max_pages=2, depth=1))
        session = runner.store.create("robot")
        round_id = runner.submit(session, "robot", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "partial"
        assert corpus.search("robot snippet") and corpus.search("robot detail")
        corpus.close()
    asyncio.run(scenario())
