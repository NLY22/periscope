import asyncio
import json

from src.corpus.store import Corpus


def test_followup_reuses_matching_evidence_and_identifies_its_origin(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer

    async def scenario():
        corpus = Corpus(scratch_path / "reuse.db")
        rows = {"first": [{"source": "web_crawl", "url": "https://example.org/robot", "text": "robot admission evidence"},
                          {"source": "web_crawl", "url": "https://example.org/weather", "text": "weather forecast"}],
                "robot admission": []}
        started, release = asyncio.Event(), asyncio.Event()
        async def collect(message):
            if message == "robot admission":
                started.set()
                await release.wait()
            if rows[message]:
                yield rows[message]
        class Client:
            async def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                assert payload["evidence"][0]["source"]["text"] == "robot admission evidence"
                assert payload["evidence"][0]["origin"]["kind"] == "reused"
                return '{"answer":"Evidence [1]"}'
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot admission")
        old = runner.submit(inv, "first", "first")
        await runner.wait(old)
        runner.answerer = EvidenceAnswerer(Client())
        new = runner.submit(inv, "robot admission", "second")
        await started.wait()
        events = runner.store.events(new)
        assert len(events) == 1 and events[0]["origin"]["round_id"] == old
        release.set()
        await runner.wait(new)
        answer = runner.store.answer(new)
        assert answer["status"] == "completed" and answer["citations"][0]["origin"]["round_id"] == old
        corpus.close()
    asyncio.run(scenario())


def test_reuse_respects_explicit_site_restriction(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    async def scenario():
        corpus = Corpus(scratch_path / "scope.db")
        async def collect(message):
            if message == "first":
                yield [{"source": "web_crawl", "url": "https://example.org/robot", "text": "robot research"},
                       {"source": "web_crawl", "url": "https://another.example/robot", "text": "robot research"}]
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        await runner.wait(runner.submit(inv, "first", "first"))
        new = runner.submit(inv, "robot", "second", options={"seeds": ["https://example.org/"]})
        await runner.wait(new)
        refs = runner.store.references(new)
        from periscope.evidence.versions import EvidenceVersions
        assert len(refs) == 1 and EvidenceVersions(corpus).get_version(**refs[0])["url"] == "https://example.org/robot"
        corpus.close()
    asyncio.run(scenario())


def test_reuse_from_shared_library_and_generic_followup(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions
    async def scenario():
        corpus = Corpus(scratch_path / "shared.db")
        async def collect(message):
            if message == "source":
                yield [{"source": "web_crawl", "url": "https://example.org/robot", "text": "robot admissions"}]
        runner = InvestigationRunner(corpus, collect)
        first = runner.store.create("other investigation")
        old = runner.submit(first, "source", "one")
        await runner.wait(old)
        second = runner.store.create("robot admissions")
        new = runner.submit(second, "还有哪些？", "two")
        await runner.wait(new)
        ref = runner.store.references(new)[0]
        assert EvidenceVersions(corpus).get_version(**ref)["text"] == "robot admissions"
        assert runner.store.origin(new, ref)["round_id"] == old
        assert runner.store.origin(new, ref)["investigation_id"] == first
        corpus.close()
    asyncio.run(scenario())


def test_recollected_same_version_emits_new_observation_event(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    async def scenario():
        corpus = Corpus(scratch_path / "refresh.db")
        async def collect(message):
            yield [{"source": "web_crawl", "url": "https://example.org/robot", "text": "robot admission"}]
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        old = runner.submit(inv, "robot", "one")
        await runner.wait(old)
        new = runner.submit(inv, "robot", "two")
        await runner.wait(new)
        events = runner.store.events(new)
        assert len(events) == 2
        assert events[0]["origin"]["kind"] == "reused"
        assert events[1]["type"] == "evidence_refreshed" and events[1]["origin"]["kind"] == "collected"
        assert events[0]["references"] == events[1]["references"]
        # Restart replay must not duplicate the observation event.
        from periscope.evidence.journal import replay_batch
        with corpus._conn:
            corpus._conn.execute("UPDATE investigation_rounds SET status='failed' WHERE id=?", (new,))
        batch = corpus._conn.execute("SELECT batch_id FROM investigation_refresh_events WHERE round_id=?", (new,)).fetchone()[0]
        path = (scratch_path / "evidence-journal" / (batch + ".jsonl"))
        replay_batch(corpus, path)
        assert len(runner.store.events(new)) == 2
        corpus.close()
    asyncio.run(scenario())


def test_automatic_v2ex_scope_excludes_other_platform_history(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    async def scenario():
        corpus = Corpus(scratch_path / "platform.db")
        async def collect(message):
            if message == "first":
                yield [{"source": "web_crawl", "url": "https://example.org/robot", "text": "robot research"}]
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        await runner.wait(runner.submit(inv, "first", "one"))
        new = runner.submit(inv, "robot", "two", options={"seeds": []})
        await runner.wait(new)
        assert runner.store.references(new) == []
        corpus.close()
    asyncio.run(scenario())


def test_search_card_does_not_hide_older_full_topic(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer
    async def scenario():
        corpus = Corpus(scratch_path / "card.db")
        async def collect(message):
            level = "full" if message == "first" else "search_result"
            yield [{"source": "v2ex_search", "url": "https://www.v2ex.com/t/1", "title": "robot",
                    "text": "robot " + level, "topic_detail": {"topic_id": "1", "content_level": level}}]
        class Client:
            async def complete(self, **kwargs):
                items = json.loads(kwargs["user"])["evidence"]
                assert {(i["source"]["content_level"], i["origin"]["kind"]) for i in items} == {
                    ("full", "reused"), ("search_result", "collected")}
                return '{"answer":"Evidence [1] [2]"}'
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        await runner.wait(runner.submit(inv, "first", "one"))
        runner.answerer = EvidenceAnswerer(Client())
        new = runner.submit(inv, "robot", "two")
        await runner.wait(new)
        assert runner.store.answer(new)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_third_round_reuses_both_historical_full_topic_and_newer_card(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.evidence.versions import EvidenceVersions
    async def scenario():
        corpus = Corpus(scratch_path / "three.db")
        async def collect(message):
            if message != "third robot":
                level = "full" if message == "first" else "search_result"
                yield [{"source": "v2ex_search", "url": "https://www.v2ex.com/t/1", "title": "robot",
                        "text": "robot " + level, "topic_detail": {"topic_id": "1", "content_level": level}}]
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        await runner.wait(runner.submit(inv, "first", "one"))
        await runner.wait(runner.submit(inv, "robot", "two"))
        third = runner.submit(inv, "third robot", "three")
        await runner.wait(third)
        levels = {EvidenceVersions(corpus).get_version(**ref)["content_level"] for ref in runner.store.references(third)}
        assert levels == {"full", "search_result"}
        corpus.close()
    asyncio.run(scenario())


def test_original_event_time_stays_fixed_after_same_version_recollected(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    async def scenario():
        corpus = Corpus(scratch_path / "event-times.db")
        observed, release = asyncio.Event(), asyncio.Event()
        async def collect(message):
            row = {"source": "web_crawl", "url": "https://example.org/robot", "text": "robot"}
            yield [row]
            observed.set()
            await release.wait()
            yield [row]
        runner = InvestigationRunner(corpus, collect)
        rid = runner.submit(runner.store.create("robot"), "robot", "one")
        await observed.wait()
        initial = runner.store.events(rid)[0]
        release.set()
        await runner.wait(rid)
        events = runner.store.events(rid)
        assert len(events) == 2 and events[1]["type"] == "evidence_refreshed"
        assert events[0]["origin"] == initial["origin"]
        assert events[1]["origin"]["observed_at"] >= events[0]["origin"]["observed_at"]
        corpus.close()
    asyncio.run(scenario())


def test_reuse_explicit_site_with_port(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    async def scenario():
        corpus = Corpus(scratch_path / "port.db")
        async def collect(message):
            if message == "first":
                yield [{"source": "web_crawl", "url": "https://example.org:8443/robot", "text": "robot"}]
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        await runner.wait(runner.submit(inv, "first", "one"))
        rid = runner.submit(inv, "robot", "two", options={"seeds": ["https://example.org:8443/"]})
        await runner.wait(rid)
        assert len(runner.store.references(rid)) == 1
        corpus.close()
    asyncio.run(scenario())


def test_answer_keeps_most_relevant_history_when_new_evidence_is_abundant(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.answer import EvidenceAnswerer
    async def scenario():
        corpus = Corpus(scratch_path / "ranking.db")
        async def collect(message):
            if message == "first":
                yield [{"source": "web_crawl", "url": "https://example.org/strong", "text": "robot admission"}] + [
                    {"source": "web_crawl", "url": f"https://example.org/weak{i}", "text": "robot only"} for i in range(10)]
            else:
                yield [{"source": "web_crawl", "url": f"https://example.org/new{i}", "text": "robot new"} for i in range(20)]
        class Client:
            async def complete(self, **kwargs):
                items = json.loads(kwargs["user"])["evidence"]
                history = [i for i in items if i["origin"]["kind"] == "reused"]
                assert len(items) == 20 and history[0]["source"]["text"] == "robot admission"
                return '{"answer":"Result [1]"}'
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        await runner.wait(runner.submit(inv, "first", "one"))
        runner.answerer = EvidenceAnswerer(Client())
        rid = runner.submit(inv, "robot admission", "two")
        await runner.wait(rid)
        assert runner.store.answer(rid)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_recovery_replays_observations_in_time_order_not_filename_order(scratch_path):
    from periscope.evidence.journal import replay_directory
    from periscope.investigations.store import InvestigationStore
    corpus = Corpus(scratch_path / "recovery.db")
    store = InvestigationStore(corpus)
    rid = store.start_round(store.create("robot"), "robot", "one")
    directory = scratch_path / "journal"
    directory.mkdir()
    row = {"source": "web_crawl", "kind": "media", "title": "robot", "url": "https://example.org/robot", "text": "robot"}
    for name, observed in [("z-old", "2026-01-01T00:00:00+00:00"), ("a-new", "2026-01-02T00:00:00+00:00")]:
        payload = {"version": 1, "batch_id": name, "round_id": rid, "created_at": observed, "rows": [row]}
        (directory / (name + ".jsonl")).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert replay_directory(corpus, directory)["batches"] == 2
    events = store.events(rid)
    assert [e["origin"]["observed_at"] for e in events] == ["2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"]
    assert [e["type"] for e in events] == ["evidence", "evidence_refreshed"]
    corpus.close()


def test_many_duplicate_observations_do_not_hide_relevant_older_source(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    async def scenario():
        corpus = Corpus(scratch_path / "many.db")
        async def collect(message):
            if message == "first":
                yield [{"source": "web_crawl", "url": "https://example.org/robot", "text": "robot admissions"},
                       {"source": "web_crawl", "url": "https://example.org/weather", "text": "weather forecast"}]
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("robot")
        old = runner.submit(inv, "first", "one")
        await runner.wait(old)
        ref = runner.store.references(old)[1]
        conn = corpus._conn
        with conn:
            conn.executemany("INSERT INTO investigation_batches VALUES (?, ?)", [(old, f"noise-{i}") for i in range(2001)])
            conn.executemany("INSERT INTO evidence_observations VALUES (?, ?, ?, ?, ?, ?)",
                [(f"noise-{i}", ref["object_id"], ref["version_id"], f"9999-{i:04d}", "{}", "") for i in range(2001)])
        new = runner.submit(inv, "robot", "two")
        await runner.wait(new)
        assert len(runner.store.references(new)) == 1
        corpus.close()
    asyncio.run(scenario())
