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
