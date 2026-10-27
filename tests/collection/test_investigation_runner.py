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
