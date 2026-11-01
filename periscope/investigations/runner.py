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
from .reuse import find_history
from .planning import QueryPlanner
from .pending import pending_candidates
from .events import SourceProgress


def _resume_budget(remaining: float) -> float:
    """Reserve most of the round for discovery after revisiting prior leads."""
    return max(0.0, min(40.0, remaining * 0.4))


class InvestigationRunner:
    """Run one cancellable collector per investigation; persist every batch."""

    def __init__(self, corpus, collector: Callable | None = None, *, budget: float = 120, clock: Callable = time.monotonic, answerer=None, planner=None):
        if not 30 <= budget <= 180:
            raise ValueError("budget must be between 30 and 180 seconds")
        self.corpus = corpus
        self.collector = collector
        self.budget = budget
        self.clock = clock
        self.answerer = answerer
        self.planner = planner or QueryPlanner()
        self.store = InvestigationStore(corpus)
        journal = Path(corpus.path).resolve().parent / "evidence-journal"
        self.recovery = replay_directory(corpus, journal, owner_only=True) if journal.is_dir() else None
        self.store.interrupt_abandoned_rounds()
        self._tasks: dict[str, asyncio.Task] = {}
        self._sessions: dict[str, str] = {}

    def submit(self, investigation_id: str, message: str, request_key: str, *,
               collector: Callable | None = None, budget: float | None = None, options: dict | None = None,
               plan_collector: Callable | None = None, resume_collector: Callable | None = None) -> str:
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
        self._tasks[round_id] = asyncio.create_task(self._run(round_id, message, selected, selected_budget, plan_collector, resume_collector))
        return round_id

    async def _run(self, round_id: str, message: str, collector: Callable, budget: float,
                   plan_collector: Callable | None, resume_collector: Callable | None) -> None:
        deadline = self.clock() + budget
        status, reason = "completed", ""
        resume_errors = []
        batch_warnings = []
        try:
            async with asyncio.timeout(budget):
                plan = await self.planner.plan(message, self.store.context(round_id))
                self.store.save_query_plan(round_id, plan.query, plan.variants if plan_collector else ())
                for item in find_history(self.store, round_id, plan.query):
                    self.store.attach_reused(round_id, item)
                options = self.store.get_round(round_id)["options"]
                resume_allowed = not options.get("seeds") and options.get("max_pages") is None and options.get("depth", 2) > 0
                if resume_collector and resume_allowed:
                    pending = pending_candidates(self.store, round_id, plan.query)
                    if pending:
                        for candidate in pending:
                            candidate["max_depth"] = options.get("depth", 2)
                        remaining = _resume_budget(deadline - self.clock())
                        try:
                            async with asyncio.timeout(remaining), aclosing(resume_collector(plan.query, pending, remaining)) as batches:
                                async for rows in batches:
                                    if self.store.get_round(round_id)["status"] != "running":
                                        return
                                    if self.clock() >= deadline:
                                        raise TimeoutError
                                    if isinstance(rows, SourceProgress):
                                        self.store.add_source_progress(round_id, rows.source, rows.status, rows.reason)
                                    else:
                                        outcome = save_batch(self.corpus, rows, round_id=round_id)
                                        if outcome.get("skipped"):
                                            batch_warnings.append("invalid_or_unfiled_rows")
                        except CollectionIncomplete as exc:
                            resume_errors.append(str(exc))
                        except TimeoutError:
                            resume_errors.append("resume:time_budget")
                stream = plan_collector(plan.query, plan.variants) if plan_collector else collector(plan.query)
                async with aclosing(stream) as batches:
                    async for rows in batches:
                        if self.store.get_round(round_id)["status"] != "running":
                            return
                        if self.clock() >= deadline:
                            raise TimeoutError
                        if isinstance(rows, SourceProgress):
                            self.store.add_source_progress(round_id, rows.source, rows.status, rows.reason)
                        else:
                            outcome = save_batch(self.corpus, rows, round_id=round_id)
                            if outcome.get("skipped"):
                                batch_warnings.append("invalid_or_unfiled_rows")
        except asyncio.CancelledError:
            self.store.finish_round(round_id, "cancelled")
            return
        except TimeoutError:
            status = "partial"
        except CollectionIncomplete as exc:
            reason = str(exc)
            status, reason = "partial", reason if reason.startswith(("v2ex", "hn:", "sources:")) else "collection_incomplete"
        except Exception as exc:
            self.store.finish_round(round_id, "failed", type(exc).__name__)
            return
        if resume_errors:
            status = "partial"
            reason = ("sources:" + ",".join([*resume_errors, reason] if reason else resume_errors))[:200]
        if batch_warnings:
            status = "partial"
            reason = (reason + "," if reason else "sources:") + "invalid_or_unfiled_rows"
            reason = reason[:200]
        if status == "partial":
            self.store.close_running_sources(round_id, "partial", reason or "time_budget")
        self.store.save_collection_result(round_id, status, reason)
        try:
            if self.answerer is not None:
                try:
                    async with asyncio.timeout(60):
                        answer = await self.answerer.answer(self.corpus, message, self.store.references(round_id),
                                                           context=self.store.context(round_id),
                                                           collection_status=status, collection_reason=reason,
                                                           coverage=lambda ref: self.store.coverage(round_id, ref),
                                                           origin=lambda ref: self.store.origin(round_id, ref))
                except Exception as exc:
                    answer = {"status": "failed", "text": "", "citations": [], "reason": type(exc).__name__}
                self.store.save_answer(round_id, answer)
            self.store.finish_round(round_id, status, reason)
        except asyncio.CancelledError:
            self.store.finish_round(round_id, "cancelled")

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
