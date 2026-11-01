import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from src.corpus.store import Corpus


def test_web_investigation_exposes_saved_evidence_and_events(monkeypatch, scratch_path):
    from src.web import app as web

    corpus = Corpus(scratch_path / "web.db")
    async def collect(topic, options):
        yield [{"source": "web_crawl", "kind": "media", "title": "robot",
                "url": "https://example.org/", "text": "robot admission evidence"}]

    monkeypatch.setattr(web, "stream_crawl", collect)
    orch = SimpleNamespace(get_corpus=lambda: corpus)
    with TestClient(web.create_app(orch), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        created = client.post("/api/investigations", json={"question": "robot"})
        assert created.status_code == 200
        investigation = created.json()["investigation_id"]
        assert any(item["id"] == investigation for item in client.get("/api/investigations").json()["investigations"])
        submitted = client.post(f"/api/investigations/{investigation}/rounds",
                                json={"message": "robot", "request_key": "one", "seeds": ["https://example.org/"]})
        assert submitted.status_code == 200
        changed = client.post(f"/api/investigations/{investigation}/rounds",
                              json={"message": "robot", "request_key": "one", "seeds": ["https://other.example/"]})
        assert changed.status_code == 400
        round_id = submitted.json()["round_id"]
        assert client.get(f"/api/investigations/{investigation}").json()["rounds"][0]["id"] == round_id
        for _ in range(100):
            result = client.get(f"/api/investigation-rounds/{round_id}").json()
            if result["round"]["status"] != "running":
                break
            time.sleep(0.02)
        assert result["round"]["status"] == "completed"
        assert len(result["events"]) == 1
        reference = result["events"][0]["references"][0]
        evidence = client.get(f'/api/evidence/{reference["object_id"]}', params={"version": reference["version_id"]})
        assert evidence.status_code == 200
        assert evidence.json()["version"]["text"] == "robot admission evidence"
        cursor = result["events"][0]["seq"]
        assert client.get(f"/api/investigation-rounds/{round_id}", params={"after": cursor}).json()["events"] == []
        assert client.post("/api/investigations", headers={"Origin": "https://evil.example"},
                           json={"question": "robot"}).status_code == 403
        assert client.post("/api/investigations", headers={"Host": "evil.example"},
                           json={"question": "robot"}).status_code == 403
    corpus.close()


def test_web_can_stop_running_round_without_losing_saved_evidence(monkeypatch, scratch_path):
    import asyncio
    from src.web import app as web

    corpus = Corpus(scratch_path / "stop.db")
    async def collect(topic, options):
        yield [{"source": "web_crawl", "kind": "media", "title": "robot",
                "url": "https://example.org/", "text": "robot first batch"}]
        await asyncio.Future()
    monkeypatch.setattr(web, "stream_crawl", collect)
    with TestClient(web.create_app(SimpleNamespace(get_corpus=lambda: corpus)), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        investigation = client.post("/api/investigations", json={"question": "robot"}).json()["investigation_id"]
        round_id = client.post(f"/api/investigations/{investigation}/rounds",
                               json={"message": "robot", "request_key": "first", "seeds": ["https://example.org/"]}).json()["round_id"]
        for _ in range(100):
            status = client.get(f"/api/investigation-rounds/{round_id}").json()
            if status["events"]:
                break
            time.sleep(0.02)
        assert status["events"]
        assert client.post(f"/api/investigation-rounds/{round_id}/stop").json()["status"] == "cancelled"
        assert client.post(f"/api/investigation-rounds/{round_id}/stop").json()["status"] == "cancelled"
        assert client.get(f"/api/investigation-rounds/{round_id}").json()["events"]
    corpus.close()


def test_mcp_uses_same_backend_round_and_evidence(monkeypatch, scratch_path):
    import asyncio
    import httpx
    from src.web import app as web
    from src.mcp import server

    corpus = Corpus(scratch_path / "shared.db")
    async def collect(topic, options):
        yield [{"source": "web_crawl", "kind": "media", "title": "robot",
                "url": "https://example.org/", "text": "robot shared evidence"}]
    monkeypatch.setattr(web, "stream_crawl", collect)
    app = web.create_app(SimpleNamespace(get_corpus=lambda: corpus))
    client_type = httpx.AsyncClient
    monkeypatch.setenv("PERISCOPE_BACKEND_URL", "http://localhost")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))))

    async def scenario():
        invalid = await server.ps_investigation_create(" ")
        assert invalid["error"]["code"] == "PS_INVALID_INPUT"
        assert "question" in invalid["error"]["message"]
        created = await server.ps_investigation_create("robot")
        investigation = created["data"]["investigation_id"]
        listing = await server.ps_investigation_list()
        assert any(item["id"] == investigation for item in listing["data"]["investigations"])
        submitted = await server.ps_investigation_submit(investigation, "robot", "key", ["https://example.org/"])
        round_id = submitted["data"]["round_id"]
        details = await server.ps_investigation_get(investigation)
        assert details["data"]["rounds"][0]["id"] == round_id
        for _ in range(100):
            result = await server.ps_investigation_status(round_id)
            if result["data"]["round"]["status"] != "running":
                break
            await asyncio.sleep(0.02)
        assert result["ok"] and result["data"]["round"]["status"] == "completed"
        reference = result["data"]["events"][0]["references"][0]
        evidence = await server.ps_investigation_evidence(**reference)
        assert evidence["ok"] and evidence["data"]["version"]["text"] == "robot shared evidence"
    asyncio.run(scenario())
    corpus.close()


def test_mcp_refuses_nonlocal_investigation_backend(monkeypatch):
    import asyncio
    from src.mcp.server import ps_investigation_create

    monkeypatch.setenv("PERISCOPE_BACKEND_URL", "http://example.org")
    response = asyncio.run(ps_investigation_create("robot"))
    assert response["ok"] is False
    assert response["error"]["code"] == "PS_INVALID_INPUT"


def test_remote_client_cannot_spoof_local_host_header(scratch_path):
    from src.web import app as web

    corpus = Corpus(scratch_path / "remote.db")
    app = web.create_app(SimpleNamespace(get_corpus=lambda: corpus))
    with TestClient(app, base_url="http://localhost", client=("203.0.113.8", 50000)) as client:
        assert client.post("/api/investigations", json={"question": "robot"}).status_code == 403
    corpus.close()


def test_web_can_discover_v2ex_without_a_seed_url(monkeypatch, scratch_path):
    from src.web import app as web

    corpus = Corpus(scratch_path / "discovery.db")
    async def search(topic, *, budget, max_pages=None, depth, variants=(), emit_progress=False):
        assert max_pages is None
        yield [{"source": "v2ex_search", "kind": "community", "title": "robot question",
                "url": "https://www.v2ex.com/t/123", "text": "robot result",
                "topic_detail": {"topic_id": "123", "content_level": "search_result"}}]
    monkeypatch.setattr(web, "stream_auto_search", search)
    with TestClient(web.create_app(SimpleNamespace(get_corpus=lambda: corpus)),
                    base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        investigation = client.post("/api/investigations", json={"question": "robot"}).json()["investigation_id"]
        response = client.post(f"/api/investigations/{investigation}/rounds",
                               json={"message": "robot", "request_key": "one"})
        assert response.status_code == 200
        round_id = response.json()["round_id"]
        for _ in range(100):
            status = client.get(f"/api/investigation-rounds/{round_id}").json()
            if status["round"]["status"] != "running":
                break
            time.sleep(0.02)
        assert status["round"]["status"] == "completed" and status["events"]
    corpus.close()


def test_web_continue_deepens_previously_discovered_candidate(monkeypatch, scratch_path):
    from src.web import app as web

    corpus = Corpus(scratch_path / "web-continue.db")
    row = {"source": "github_issues", "kind": "community", "_origin": "platform",
           "title": "python issue", "url": "https://github.com/example/repo/issues/1", "text": "python excerpt",
           "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#1", "content_level": "search_result"}}
    searches, resumed = [], []

    async def search(topic, *, budget, max_pages=None, depth, variants=(), emit_progress=False):
        searches.append(topic)
        if len(searches) == 1:
            yield [row]

    async def resume(topic, candidates, budget):
        resumed.extend(candidates)
        yield [{**row, "text": "python full detail", "evidence": {**row["evidence"], "content_level": "full"}}]

    monkeypatch.setattr(web, "stream_auto_search", search)
    monkeypatch.setattr(web, "stream_pending_candidates", resume)
    with TestClient(web.create_app(SimpleNamespace(get_corpus=lambda: corpus)), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        inv = client.post("/api/investigations", json={"question": "python"}).json()["investigation_id"]
        for message, key in (("python", "first"), ("继续", "second")):
            rid = client.post(f"/api/investigations/{inv}/rounds", json={"message": message, "request_key": key}).json()["round_id"]
            for _ in range(100):
                status = client.get(f"/api/investigation-rounds/{rid}").json()
                if status["round"]["status"] != "running":
                    break
                time.sleep(0.02)
            assert status["round"]["status"] == "completed"
        assert resumed and searches == ["python", "python"]
        assert corpus.search("python full detail")
    corpus.close()


def test_continue_inherits_previous_seed_scope_when_it_was_not_specified(monkeypatch, scratch_path):
    from src.web import app as web

    corpus = Corpus(scratch_path / "web-scope.db")
    seen = []

    async def scoped(topic, options):
        seen.append(options.seeds)
        if False:
            yield []

    async def automatic(topic, **kwargs):
        raise AssertionError("continue should not expand the previous source restriction")
        yield []

    monkeypatch.setattr(web, "stream_crawl", scoped)
    monkeypatch.setattr(web, "stream_auto_search", automatic)
    with TestClient(web.create_app(SimpleNamespace(get_corpus=lambda: corpus)), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        inv = client.post("/api/investigations", json={"question": "python"}).json()["investigation_id"]
        first = client.post(f"/api/investigations/{inv}/rounds", json={"message": "python", "request_key": "one", "seeds": ["https://github.com/example/repo/issues/1"]}).json()["round_id"]
        for _ in range(100):
            if client.get(f"/api/investigation-rounds/{first}").json()["round"]["status"] != "running":
                break
            time.sleep(0.02)
        payload = {"message": "继续", "request_key": "two"}
        second = client.post(f"/api/investigations/{inv}/rounds", json=payload).json()["round_id"]
        for _ in range(100):
            result = client.get(f"/api/investigation-rounds/{second}").json()
            if result["round"]["status"] != "running":
                break
            time.sleep(0.02)
        assert result["round"]["status"] == "completed"
        assert seen == [("https://github.com/example/repo/issues/1",)] * 2
        assert client.post(f"/api/investigations/{inv}/rounds", json=payload).json()["round_id"] == second
    corpus.close()


def test_source_progress_and_evidence_share_a_persistent_ordered_event_cursor(scratch_path):
    import asyncio
    from periscope.investigations.events import SourceProgress
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "source-progress.db")

        async def collect(query):
            yield SourceProgress("github_issues", "running", "")
            yield [{"source": "web_crawl", "kind": "media", "title": "python",
                    "url": "https://example.org/python", "text": "python evidence"}]
            yield SourceProgress("github_issues", "partial", "candidate_limit")

        runner = InvestigationRunner(corpus, collect)
        rid = runner.submit(runner.store.create("python"), "python", "one")
        await runner.wait(rid)
        events = runner.store.events(rid)
        assert [event["type"] for event in events] == ["source_progress", "evidence", "source_progress"]
        assert events[0]["source"] == "github_issues" and events[0]["status"] == "running"
        assert events[2]["status"] == "partial" and events[2]["reason"] == "candidate_limit"
        assert runner.store.events(rid, after=events[0]["seq"]) == events[1:]
        corpus.close()
        corpus = Corpus(scratch_path / "source-progress.db")
        assert InvestigationRunner(corpus).store.events(rid) == events
        corpus.close()
    asyncio.run(scenario())


def test_evidence_event_exposes_discussion_coverage(scratch_path):
    import asyncio
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "event-coverage.db")

        async def collect(query):
            yield [{"source": "v2ex_search", "url": "https://www.v2ex.com/t/123", "title": "topic",
                    "text": "body", "topic_detail": {"topic_id": "123"},
                    "discussion_coverage": {"status": "partial", "fetched_comments": 1,
                                            "truncated_by": ["comment_limit"]},
                    "comments": [{"id": "1", "text": "reply"}]}]

        runner = InvestigationRunner(corpus, collect)
        rid = runner.submit(runner.store.create("topic"), "topic", "one")
        await runner.wait(rid)
        event = runner.store.events(rid)[0]
        assert event["coverage"]["status"] == "partial"
        assert event["coverage"]["fetched_comments"] == 1
        corpus.close()
    asyncio.run(scenario())


def test_source_progress_closes_when_round_is_stopped(scratch_path):
    import asyncio
    from periscope.investigations.events import SourceProgress
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "source-stop.db")
        started = asyncio.Event()

        async def collect(query):
            yield SourceProgress("github_issues", "running")
            started.set()
            await asyncio.Future()

        runner = InvestigationRunner(corpus, collect)
        rid = runner.submit(runner.store.create("python"), "python", "one")
        await asyncio.wait_for(started.wait(), 2)
        runner.stop(rid)
        await runner.wait(rid)
        events = runner.store.events(rid)
        assert events[-1]["source"] == "github_issues" and events[-1]["status"] == "cancelled"
        corpus.close()
    asyncio.run(scenario())


def test_source_progress_closes_on_timeout_and_backend_restart(scratch_path):
    import asyncio
    from periscope.investigations.events import SourceProgress
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "source-restart.db")
        now = [0.0]

        async def collect(query):
            yield SourceProgress("github_issues", "running")
            now[0] = 31.0
            yield [{"source": "web_crawl", "url": "https://example.org/", "text": "late"}]

        runner = InvestigationRunner(corpus, collect, budget=30, clock=lambda: now[0])
        inv = runner.store.create("python")
        rid = runner.submit(inv, "python", "one")
        await runner.wait(rid)
        assert runner.store.events(rid)[-1]["status"] == "partial"
        abandoned = runner.store.start_round(inv, "more", "two")
        runner.store.add_source_progress(abandoned, "csdn_search", "running")
        runner.store.interrupt_abandoned_rounds()
        assert runner.store.events(abandoned)[-1]["status"] == "interrupted"
        corpus.close()
    asyncio.run(scenario())


def test_new_instruction_closes_previous_round_source_progress(scratch_path):
    import asyncio
    from periscope.investigations.events import SourceProgress
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "source-replaced.db")
        started = asyncio.Event()

        async def collect(query):
            if query == "first":
                yield SourceProgress("github_issues", "running")
                started.set()
                await asyncio.Future()
            if False:
                yield []

        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("topic")
        first = runner.submit(inv, "first", "one")
        await asyncio.wait_for(started.wait(), 2)
        second = runner.submit(inv, "second", "two")
        await runner.wait(first)
        await runner.wait(second)
        assert runner.store.get_round(first)["status"] == "cancelled"
        assert runner.store.events(first)[-1]["status"] == "cancelled"
        corpus.close()
    asyncio.run(scenario())
