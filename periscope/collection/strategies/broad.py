"""Bounded retrieval across native search endpoints and relevant feed entries."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import re
import time
from urllib.parse import quote, urlsplit

import httpx

from ..transport import client as http
from ..registry import SourceEntry, SourcePlan


class _PacedClient:
    """Keep request spacing across different recipes sharing one host."""

    def __init__(self, client, host: str, interval: float, last_requests: dict):
        self.client, self.host, self.interval, self.last_requests = client, host, interval, last_requests

    async def _request(self, method: str, *args, **kwargs):
        previous = self.last_requests.get(self.host)
        if previous is not None:
            tick, interval = previous
            await asyncio.sleep(max(0, max(interval, self.interval) - (time.monotonic() - tick)))
        self.last_requests[self.host] = (time.monotonic(), self.interval)
        return await getattr(self.client, method)(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return await self._request("get", *args, **kwargs)

    async def post(self, *args, **kwargs):
        return await self._request("post", *args, **kwargs)


def _relevant(row: dict, topic: str) -> bool:
    normalize = lambda value: re.sub(r"\s+", "", value).casefold()
    haystack = normalize((row.get("title") or "") + " " + (row.get("text") or ""))
    terms = [normalize(term) for term in topic.split() if term]
    return bool(terms) and all(term in haystack for term in terms)


def _prepare(entry: SourceEntry, topic: str, pages: int):
    source = entry.source
    url = source.url
    if source.key.startswith("googlenews_"):
        url = url.replace("+after:{after_iso}", "").replace("+before:{before_iso}", "")
    changes = dict(url=url, max_pages=min(max(1, pages), source.max_pages),
                   window_days=0, windows=0)
    if any(token in url for token in ("{after}", "{before}", "{after_iso}", "{before_iso}")):
        changes.update(window_days=source.window_days or 30, windows=1)
    if entry.mode == "search":
        changes.update(queries=(topic,), query_param="query")
    else:
        changes.update(queries=())
    if source.key == "hn_comments":
        changes.update(url_field="objectID", id_fields=("objectID",))
    return replace(source, **changes)


def _linked_row(entry: SourceEntry, row: dict, observed: str) -> dict | None:
    row = dict(row)
    url = str(row.get("url") or "").strip()
    if entry.key == "hf_model_search":
        url = "https://huggingface.co/" + quote(url, safe="/") if url else ""
    elif entry.key == "hf_dataset_search":
        url = "https://huggingface.co/datasets/" + quote(url, safe="/") if url else ""
    elif entry.key == "hn_comments" and url:
        url = "https://news.ycombinator.com/item?id=" + quote(url, safe="")
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
        return None
    row.update(url=url, source=entry.key, kind=entry.kind)
    row["provenance"] = {"source_key": entry.key, "method": entry.mode,
                         "observed_at": observed, "url": url,
                         "identity": "url",
                         "content_role": "community" if entry.key == "hn_comments" else "source_text"}
    return row


async def collect_async(topic: str, plan: SourcePlan, *, pages: int = 1,
                        budget: float = 30, timeout: float = 15,
                        concurrency: int = 8) -> tuple[list[dict], dict]:
    if not topic.strip():
        raise ValueError("topic must not be empty")
    if budget < 0 or timeout <= 0 or concurrency < 1:
        raise ValueError("invalid collection budget, timeout or concurrency")
    started = time.monotonic()
    observed = datetime.now(timezone.utc).isoformat()
    semaphore = asyncio.Semaphore(concurrency)
    hosts: dict[str, asyncio.Semaphore] = {}
    last_requests = {}
    reports = {entry.key: {**entry.describe(), "status": "over_budget", "attempted": False,
                          "rows": 0, "raw_rows": 0, "filtered": 0, "unlinked": 0,
                          "elapsed": 0.0, "error": ""} for entry in plan.selected}
    for entry in plan.skipped:
        reports[entry.key] = {**entry.describe(), "status": "skipped", "attempted": False,
                              "rows": 0, "reason": plan.reasons.get(entry.key) or entry.describe()["limitation"] or "not_topic_searchable"}
    batches = {}

    async def one(client: httpx.AsyncClient, entry: SourceEntry) -> None:
        host = urlsplit(entry.source.url).hostname or entry.key
        host_semaphore = hosts.setdefault(host, asyncio.Semaphore(1))
        # Waiting for a host must not consume the global concurrency allowance.
        async with host_semaphore, semaphore:
            report = reports[entry.key]
            report["attempted"] = True
            tick = time.monotonic()
            def capture(rows, stats):
                report.update(raw_rows=len(rows), requests=stats.requests, filtered=stats.filtered)
                report["unlinked"] = 0
                accepted = []
                for row in rows:
                    if entry.mode != "search" and not _relevant(row, topic):
                        report["filtered"] += 1
                        continue
                    linked = _linked_row(entry, row, observed)
                    if linked is None:
                        report["unlinked"] += 1
                    else:
                        accepted.append(linked)
                batches[entry.key] = accepted
                report.update(rows=len(accepted))

            try:
                paced = _PacedClient(client, host, entry.source.min_interval, last_requests)
                rows, stats = await asyncio.wait_for(http.collect(paced, _prepare(entry, topic, pages), on_progress=capture), timeout)
                capture(rows, stats)
                report.update(error=stats.error,
                              status="partial" if stats.error and report["rows"] else "failed" if stats.error else "ok" if report["rows"] else "empty")
            except asyncio.TimeoutError:
                report.update(status="partial" if report["rows"] else "timeout", error="source_timeout")
            except asyncio.CancelledError:
                report.update(status="partial" if report["rows"] else "over_budget", error="overall_deadline")
                raise
            except Exception as exc:
                report.update(status="failed", error=type(exc).__name__)
            finally:
                report["elapsed"] = round(time.monotonic() - tick, 3)

    async with httpx.AsyncClient(headers=http.UA, timeout=timeout, follow_redirects=True) as client:
        tasks = [asyncio.create_task(one(client, entry)) for entry in plan.selected]
        if tasks:
            if budget:
                _, pending = await asyncio.wait(tasks, timeout=budget)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                await asyncio.gather(*tasks)
    rows = [row for entry in plan.selected for row in batches.get(entry.key, [])]
    active = [reports[entry.key] for entry in plan.selected]
    per = [(r["key"], r["rows"], r["elapsed"], r["error"]) for r in active if r["attempted"] and r["status"] != "over_budget"]
    return rows, {"elapsed": time.monotonic() - started, "total": len(rows), "per": per,
                  "sources": len(plan.selected), "attempted": sum(r["attempted"] for r in active),
                  "failed": [r["key"] for r in active if r["status"] in ("failed", "partial", "timeout")],
                  "over_budget": [r["key"] for r in active if r["status"] == "over_budget" or r["error"] == "overall_deadline"],
                  "skipped_browser": [e.key for e in plan.skipped if e.mode == "browser"],
                  "budget": budget, "source_reports": list(reports.values())}


def collect_topic(topic: str, plan: SourcePlan, **kwargs) -> tuple[list[dict], dict]:
    return asyncio.run(collect_async(topic, plan, **kwargs))
