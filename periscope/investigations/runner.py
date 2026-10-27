"""Bounded in-process execution for investigation collection batches."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import aclosing
from pathlib import Path

from periscope.evidence.journal import replay_directory, save_batch
from .crawl import CollectionIncomplete
from .store import InvestigationStore


class InvestigationRunner:
    """Run one cancellable collector per investigation; persist every batch."""

    def __init__(self, corpus, collector: Callable | None = None, *, budget: float = 120, clock: Callable = time.monotonic):
        if not 30 <= budget <= 180:
            raise ValueError("budget must be between 30 and 180 seconds")
        self.corpus = corpus
        self.collector = collector
        self.budget = budget
        self.clock = clock
        self.store = InvestigationStore(corpus)
        journal = Path(corpus.path).resolve().parent / "evidence-journal"
        self.recovery = replay_directory(corpus, journal, owner_only=True) if journal.is_dir() else None
        self.store.interrupt_abandoned_rounds()
        self._tasks: dict[str, asyncio.Task] = {}
        self._sessions: dict[str, str] = {}

    def submit(self, investigation_id: str, message: str, request_key: str, *,
               collector: Callable | None = None, budget: float | None = None, options: dict | None = None) -> str:
        selected = collector or self.collector
        selected_budget = self.budget if budget is None else budget
        if selected is None or not 30 <= selected_budget <= 180:
            raise ValueError("collector and budget between 30 and 180 seconds are required")
        round_id = self.store.start_round(investigation_id, message, request_key, options=options)
        if round_id in self._tasks or self.store.get_round(round_id)["status"] != "running":
            return round_id
        old = self._sessions.get(investigation_id)
        if old and old != round_id and old in self._tasks:
            self._tasks[old].cancel()
        self._sessions[investigation_id] = round_id
        self._tasks[round_id] = asyncio.create_task(self._run(round_id, message, selected, selected_budget))
        return round_id

    async def _run(self, round_id: str, message: str, collector: Callable, budget: float) -> None:
        deadline = self.clock() + budget
        try:
            async with asyncio.timeout(budget):
                async with aclosing(collector(message)) as batches:
                    async for rows in batches:
                        if self.store.get_round(round_id)["status"] != "running":
                            return
                        if self.clock() >= deadline:
                            self.store.finish_round(round_id, "partial")
                            return
                        save_batch(self.corpus, rows, round_id=round_id)
            self.store.finish_round(round_id, "completed")
        except asyncio.CancelledError:
            self.store.finish_round(round_id, "cancelled")
        except TimeoutError:
            self.store.finish_round(round_id, "partial")
        except CollectionIncomplete as exc:
            reason = str(exc)
            self.store.finish_round(round_id, "partial", reason if reason.startswith("v2ex") else "collection_incomplete")
        except Exception as exc:
            self.store.finish_round(round_id, "failed", type(exc).__name__)

    async def wait(self, round_id: str) -> None:
        task = self._tasks.get(round_id)
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise

    def stop(self, round_id: str) -> str:
        record = self.store.get_round(round_id)
        if record is None:
            raise ValueError("round not found")
        if self.store.finish_round(round_id, "cancelled"):
            task = self._tasks.get(round_id)
            if task is not None:
                task.cancel()
        return self.store.get_round(round_id)["status"]
