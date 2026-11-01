import asyncio
import json

from src.corpus.store import Corpus


def test_answer_is_persisted_with_fixed_evidence_reference(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    async def scenario():
        corpus = Corpus(scratch_path / "answer.db")
        async def collect(message):
            yield [{"source": "web_crawl", "kind": "media", "title": "Example",
                    "url": "https://example.org/", "text": "Evidence text"}]
        class Client:
            async def complete(self, **kwargs):
                assert "Evidence text" in kwargs["user"]
                return json.dumps({"answer": "发现了相关资料 [1]。"})
        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        inv = runner.store.create("research")
        rid = runner.submit(inv, "question", "one")
        await runner.wait(rid)
        answer = runner.store.answer(rid)
        assert runner.store.get_round(rid)["status"] == "completed"
        assert answer["status"] == "completed" and answer["text"] == "发现了相关资料 [1]。"
        assert answer["citations"][0]["reference"] == runner.store.references(rid)[0]
        corpus.close()
    asyncio.run(scenario())


def test_answer_rejects_unresolvable_citations_without_losing_evidence(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    async def scenario():
        corpus = Corpus(scratch_path / "bad-answer.db")
        async def collect(message):
            yield [{"source": "web_crawl", "kind": "media", "title": "Example",
                    "url": "https://example.org/", "text": "Evidence text"}]
        class Client:
            async def complete(self, **kwargs):
                return '{"answer": "Claim [9]"}'
        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("research"), "question", "one")
        await runner.wait(rid)
        assert runner.store.get_round(rid)["status"] == "completed"
        assert runner.store.answer(rid)["status"] == "failed"
        assert len(runner.store.references(rid)) == 1
        corpus.close()
    asyncio.run(scenario())


def test_cancel_during_answer_retains_evidence_and_does_not_publish_late_answer(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    async def scenario():
        corpus = Corpus(scratch_path / "cancel-answer.db")
        started = asyncio.Event()
        from periscope.investigations.crawl import CollectionIncomplete
        async def collect(message):
            yield [{"source": "web_crawl", "url": "https://example.org/", "text": "Evidence"}]
            raise CollectionIncomplete("v2ex:rate_limited")
        class Client:
            async def complete(self, **kwargs):
                started.set()
                await asyncio.Future()
        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("research"), "question", "one")
        await asyncio.wait_for(started.wait(), 2)
        assert runner.stop(rid) == "cancelled"
        await runner.wait(rid)
        assert runner.store.answer(rid) is None
        assert len(runner.store.references(rid)) == 1
        assert runner.store.collection_result(rid) == {"status": "partial", "reason": "v2ex:rate_limited"}
        corpus.close()
    asyncio.run(scenario())


def test_answer_prioritizes_full_content_and_current_round_coverage(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer
    async def scenario():
        corpus = Corpus(scratch_path / "priority.db")
        async def collect(message):
            yield [{"source": "v2ex_search", "url": f"https://www.v2ex.com/t/{i}", "title": "Card",
                    "text": "snippet", "topic_detail": {"topic_id": str(i), "content_level": "search_result"}}
                   for i in range(1, 22)]
            yield [{"source": "v2ex_search", "url": "https://www.v2ex.com/t/1", "title": "Full",
                    "text": "full topic", "topic_detail": {"topic_id": "1", "content_level": "full"},
                    "discussion_coverage": {"status": "partial", "truncated_by": ["comment_limit"]},
                    "comments": [{"id": "99", "text": "reply text"}]}]
        class Client:
            async def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                items = payload["evidence"]
                assert any(i["source"].get("text") == "full topic" for i in items)
                assert any(i["source"].get("text") == "reply text" for i in items)
                full = next(i for i in items if i["source"].get("text") == "full topic")
                assert full["coverage"]["truncated_by"] == ["comment_limit"]
                return '{"answer":"Result [1]"}'
        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("main"), "question", "one")
        await runner.wait(rid)
        assert runner.store.answer(rid)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_answer_selects_relevant_passage_after_first_3000_chars(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    async def scenario():
        corpus = Corpus(scratch_path / "long-evidence.db")

        async def collect(message):
            yield [{"source": "web_crawl", "url": "https://example.org/important", "title": "Policy",
                    "text": ("irrelevant filler " * 250) + "The admission deadline is 30 November."}]
            yield [{"source": "web_crawl", "url": f"https://example.org/other/{i}", "title": "Other",
                    "text": "unrelated administrative text"} for i in range(25)]

        class Client:
            async def complete(self, **kwargs):
                items = json.loads(kwargs["user"])["evidence"]
                relevant = next(item for item in items if "admission deadline is 30 November" in item["source"]["text"])
                assert len(relevant["source"]["text"]) <= 3000
                assert relevant["source"]["text_excerpt_start"] > 0
                return json.dumps({"answer": f"Deadline [ {relevant['number']} ]".replace("[ ", "[").replace(" ]", "]")})

        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("admissions"), "admission deadline", "one")
        await runner.wait(rid)
        answer = runner.store.answer(rid)
        assert answer["status"] == "completed"
        ref = answer["citations"][0]["reference"]
        from periscope.evidence.versions import EvidenceVersions
        assert EvidenceVersions(corpus).get_version(**ref)["text"].endswith("The admission deadline is 30 November.")
        corpus.close()
    asyncio.run(scenario())


def test_answer_keeps_independent_sources_when_one_site_dominates(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    async def scenario():
        corpus = Corpus(scratch_path / "diverse-evidence.db")

        async def collect(message):
            yield [{"source": "web_crawl", "url": "https://official.example/admissions", "text": "admission policy"}]
            yield [{"source": "web_crawl", "url": f"https://community.example/post/{i}",
                    "text": "admission discussion"} for i in range(25)]

        class Client:
            async def complete(self, **kwargs):
                items = json.loads(kwargs["user"])["evidence"]
                assert any(item["source"]["url"] == "https://official.example/admissions" for item in items)
                return '{"answer":"Result [1]"}'

        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("admission"), "admission", "one")
        await runner.wait(rid)
        assert runner.store.answer(rid)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_answer_model_receives_community_authorship_role(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    async def scenario():
        corpus = Corpus(scratch_path / "community-role.db")

        async def collect(message):
            yield [{"source": "github_issues", "kind": "community", "title": "Issue", "text": "a personal view",
                    "url": "https://github.com/example/repo/issues/1", "author": "reader",
                    "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#1"},
                    "provenance": {"content_role": "community"}}]

        class Client:
            async def complete(self, **kwargs):
                source = json.loads(kwargs["user"])["evidence"][0]["source"]
                assert source["content_role"] == "community" and source["author"] == "reader"
                return '{"answer":"A view [1]"}'

        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("topic"), "topic", "one")
        await runner.wait(rid)
        assert runner.store.answer(rid)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_empty_locator_is_not_treated_as_answer_evidence(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    class Client:
        async def complete(self, **kwargs):
            raise AssertionError("an empty link must not be treated as evidence")

    async def scenario():
        corpus = Corpus(scratch_path / "locator-answer.db")

        async def collect(query):
            yield [{"source": "github_issues", "kind": "community", "title": "https://github.com/a/b/issues/1",
                    "url": "https://github.com/a/b/issues/1", "text": "",
                    "evidence": {"source": "github", "type": "issue", "native_id": "a/b#1", "content_level": "locator"}}]

        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("topic"), "topic", "one")
        await runner.wait(rid)
        assert runner.store.answer(rid)["reason"] == "no_evidence"
        corpus.close()
    asyncio.run(scenario())


def test_answer_uses_latest_version_for_same_object(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer
    async def scenario():
        corpus = Corpus(scratch_path / "latest.db")
        async def collect(message):
            for text in ["old content", "new content"]:
                yield [{"source": "web_crawl", "url": "https://example.org/", "text": text}]
        class Client:
            async def complete(self, **kwargs):
                items = json.loads(kwargs["user"])["evidence"]
                assert len(items) == 1 and items[0]["source"]["text"] == "new content"
                return '{"answer":"Updated [1]"}'
        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("main"), "question", "one")
        await runner.wait(rid)
        assert runner.store.answer(rid)["status"] == "completed"
        assert runner.store.answer(rid)["citations"][0]["reference"] == runner.store.references(rid)[-1]
        corpus.close()
    asyncio.run(scenario())


def test_answer_uses_last_observation_when_content_reverts(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer
    async def scenario():
        corpus = Corpus(scratch_path / "reverted.db")
        async def collect(message):
            for text in ["first", "second", "first"]:
                yield [{"source": "web_crawl", "url": "https://example.org/", "text": text}]
        class Client:
            async def complete(self, **kwargs):
                assert json.loads(kwargs["user"])["evidence"][0]["source"]["text"] == "first"
                return '{"answer":"Current [1]"}'
        runner = InvestigationRunner(corpus, collect, answerer=EvidenceAnswerer(Client()))
        rid = runner.submit(runner.store.create("main"), "question", "one")
        await runner.wait(rid)
        assert runner.store.answer(rid)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_web_status_exposes_persisted_answer(monkeypatch, scratch_path):
    import time
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from src.web import app as web

    corpus = Corpus(scratch_path / "web-answer.db")
    async def collect(topic, options):
        yield [{"source": "web_crawl", "url": "https://example.org/", "text": "Evidence"}]
    class Client:
        async def complete(self, **kwargs):
            return '{"answer":"Result [1]"}'
    monkeypatch.setattr(web, "stream_crawl", collect)
    orch = SimpleNamespace(get_corpus=lambda: corpus, _get_optional_ai_client=lambda: Client())
    with TestClient(web.create_app(orch), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        inv = client.post("/api/investigations", json={"question": "question"}).json()["investigation_id"]
        rid = client.post(f"/api/investigations/{inv}/rounds", json={"message": "question", "request_key": "one", "seeds": ["https://example.org/"]}).json()["round_id"]
        for _ in range(100):
            result = client.get(f"/api/investigation-rounds/{rid}").json()
            if result["round"]["status"] != "running":
                break
            time.sleep(0.02)
        assert result["answer"]["text"] == "Result [1]"
        ref = result["answer"]["citations"][0]["reference"]
        assert client.get(f'/api/evidence/{ref["object_id"]}', params={"version": ref["version_id"]}).json()["version"]["text"] == "Evidence"
    corpus.close()


def test_followup_context_and_partial_coverage_are_given_to_model(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer
    from periscope.investigations.crawl import CollectionIncomplete

    async def scenario():
        corpus = Corpus(scratch_path / "followup.db")
        async def collect(message):
            yield [{"source": "web_crawl", "url": "https://example.org/", "text": "Evidence"}]
            raise CollectionIncomplete("v2ex:rate_limited")
        class Client:
            async def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                assert payload["context"] == {"main_question": "main", "previous_messages": ["first"]}
                assert payload["collection_status"] == "partial"
                assert payload["collection_reason"] == "v2ex:rate_limited"
                return '{"answer":"Partial [1]"}'
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("main")
        first = runner.submit(inv, "first", "first")
        await runner.wait(first)
        runner.answerer = EvidenceAnswerer(Client())
        second = runner.submit(inv, "followup", "second")
        await runner.wait(second)
        assert runner.store.get_round(second)["status"] == "partial"
        assert runner.store.answer(second)["text"] == "Partial [1]"
        corpus.close()
    asyncio.run(scenario())
