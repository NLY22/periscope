"""Bounded in-process execution for investigation collection batches."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import aclosing

from periscope.evidence.journal import save_batch
from .crawl import CollectionIncomplete
from .store import InvestigationStore


class InvestigationRunner:
    """Run one cancellable collector per investigation; persist every batch."""

    def __init__(self, corpus, collector: Callable, *, budget: float = 120, clock: Callable = time.monotonic):
        if not 30 <= budget <= 180:
            raise ValueError("budget must be between 30 and 180 seconds")
        self.corpus = corpus
        self.collector = collector
        self.budget = budget
        self.clock = clock
        self.store = InvestigationStore(corpus)
        self._tasks: dict[str, asyncio.Task] = {}
        self._sessions: dict[str, str] = {}

    def submit(self, investigation_id: str, message: str, request_key: str) -> str:
        round_id = self.store.start_round(investigation_id, message, request_key)
        if round_id in self._tasks or self.store.get_round(round_id)["status"] != "running":
            return round_id
        old = self._sessions.get(investigation_id)
        if old and old != round_id and old in self._tasks:
            self._tasks[old].cancel()
        self._sessions[investigation_id] = round_id
        self._tasks[round_id] = asyncio.create_task(self._run(round_id, message))
        return round_id

    async def _run(self, round_id: str, message: str) -> None:
        deadline = self.clock() + self.budget
        try:
            async with asyncio.timeout(self.budget):
                async with aclosing(self.collector(message)) as batches:
                    async for rows in batches:
                        if self.store.get_round(round_id)["status"] != "running":
                            return
                        if self.clock() >= deadline:
                            self.store.finish_round(round_id, "partial")
                            return
                        result = save_batch(self.corpus, rows)
                        for reference in result["versions"]:
                            self.store.attach(round_id, **reference)
            self.store.finish_round(round_id, "completed")
        except asyncio.CancelledError:
            self.store.finish_round(round_id, "cancelled")
        except TimeoutError:
            self.store.finish_round(round_id, "partial")
        except CollectionIncomplete:
            self.store.finish_round(round_id, "partial", "collection_incomplete")
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
