import asyncio
from src.corpus.store import Corpus


def test_new_instruction_stops_old_task_and_preserves_completed_batches(scratch_path):
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "corpus.db")
        first_saved = asyncio.Event()
        release_old = asyncio.Event()
        row = {"source": "web_crawl", "kind": "media", "title": "robot",
               "url": "https://example.org/", "text": "robot first"}

        async def collect(message):
            if message == "first":
                yield [row]
                first_saved.set()
                try:
                    await release_old.wait()
                except asyncio.CancelledError:
                    yield [{**row, "text": "robot late response"}]
            else:
                yield [{**row, "text": "robot second"}]

        runner = InvestigationRunner(corpus, collect)
        investigation = runner.store.create("robot")
        old = runner.submit(investigation, "first", "key-1")
        await first_saved.wait()
        new = runner.submit(investigation, "second", "key-2")
        await runner.wait(old)
        await runner.wait(new)
        assert runner.store.get_round(old)["status"] == "cancelled"
        assert runner.store.get_round(new)["status"] == "completed"
        assert len(runner.store.references(old)) == 1
        assert len(runner.store.references(new)) == 1
        assert not corpus.search("late response")
        assert corpus.search("robot first") and corpus.search("robot second")
        corpus.close()

    asyncio.run(scenario())


def test_batch_arriving_after_deadline_is_not_saved(scratch_path):
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "deadline.db")
        now = [0.0]
        row = {"source": "web_crawl", "kind": "media", "title": "robot",
               "url": "https://example.org/", "text": "robot late result"}

        async def collect(message):
            now[0] = 121
            yield [row]

        runner = InvestigationRunner(corpus, collect, clock=lambda: now[0])
        investigation = runner.store.create("robot")
        round_id = runner.submit(investigation, "find", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "partial"
        assert not corpus.search("robot late")
        corpus.close()

    asyncio.run(scenario())


def test_collector_failure_preserves_previous_batch_and_reports_reason(scratch_path):
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "failure.db")
        async def collect(message):
            yield [{"source": "web_crawl", "kind": "media", "title": "robot",
                    "url": "https://example.org/", "text": "robot collected"}]
            raise OSError("network failed")
        runner = InvestigationRunner(corpus, collect)
        investigation = runner.store.create("robot")
        round_id = runner.submit(investigation, "find", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "failed"
        assert runner.store.get_round(round_id)["error"] == "OSError"
        assert len(runner.store.references(round_id)) == 1
        corpus.close()

    asyncio.run(scenario())


def test_immediate_replacement_does_not_surface_cancelled_task_error(scratch_path):
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "immediate.db")
        async def collect(message):
            if False:
                yield []
        runner = InvestigationRunner(corpus, collect)
        investigation = runner.store.create("robot")
        old = runner.submit(investigation, "first", "first")
        new = runner.submit(investigation, "second", "second")
        await runner.wait(old)
        await runner.wait(new)
        assert runner.store.get_round(old)["status"] == "cancelled"
        assert runner.store.get_round(new)["status"] == "completed"
        corpus.close()

    asyncio.run(scenario())


def test_backend_restart_marks_abandoned_round_interrupted(scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.store import InvestigationStore

    path = scratch_path / "restart.db"
    corpus = Corpus(path)
    store = InvestigationStore(corpus)
    investigation = store.create("robot")
    round_id = store.start_round(investigation, "find", "key")
    corpus.close()
    corpus = Corpus(path)
    runner = InvestigationRunner(corpus)
    assert runner.store.get_round(round_id)["status"] == "interrupted"
    assert runner.store.get_round(round_id)["error"] == "backend_restart"
    corpus.close()


def test_restart_recovers_saved_batch_and_its_round_reference(scratch_path, monkeypatch):
    from periscope.evidence.journal import save_batch, replay_directory
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.store import InvestigationStore

    path = scratch_path / "crash.db"
    corpus = Corpus(path)
    store = InvestigationStore(corpus)
    investigation = store.create("robot")
    round_id = store.start_round(investigation, "find", "key")
    row = {"source": "web_crawl", "kind": "media", "title": "robot",
           "url": "https://example.org/", "text": "robot preserved after crash"}
    with monkeypatch.context() as patch:
        patch.setattr(InvestigationStore, "attach", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash")))
        try:
            save_batch(corpus, [row], round_id=round_id)
        except OSError:
            pass
        else:
            raise AssertionError("expected simulated failure")
    corpus.close()
    corpus = Corpus(path)
    runner = InvestigationRunner(corpus)
    assert runner.store.get_round(round_id)["status"] == "interrupted"
    assert len(runner.store.references(round_id)) == 1
    assert len(runner.store.events(round_id)) == 1
    replay_directory(corpus, scratch_path / "evidence-journal")
    assert len(runner.store.events(round_id)) == 1
    corpus.close()


def test_automatic_recovery_does_not_import_another_corpus_journal(scratch_path):
    from periscope.evidence.journal import save_batch
    from periscope.investigations.runner import InvestigationRunner

    first = Corpus(scratch_path / "first.db")
    save_batch(first, [{"source": "web_crawl", "kind": "media", "title": "robot",
                        "url": "https://example.org/", "text": "robot private corpus"}])
    first.close()
    second = Corpus(scratch_path / "second.db")
    InvestigationRunner(second)
    assert second.search("robot private") == []
    second.close()
