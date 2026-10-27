"""Bridge the native crawler's page deliveries into investigation batches."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from periscope.collection.crawling.engine import crawl_async


class CollectionIncomplete(Exception):
    """The source reports that part of the requested crawl was not completed."""


async def stream_crawl(topic, options):
    if len(options.seeds) == 1:
        url = urlsplit(options.seeds[0])
        match = re.fullmatch(r"/t/(\d+)/?", url.path)
        if url.hostname in {"v2ex.com", "www.v2ex.com"} and match:
            from periscope.collection.platforms.v2ex import fetch_discussion
            detail, comments, coverage = await fetch_discussion(match.group(1), budget_seconds=options.budget)
            if detail or comments:
                yield [{"source": "v2ex_search", "kind": "community", "_origin": "platform",
                        "title": detail.get("title") or options.seeds[0], "url": detail.get("url") or options.seeds[0],
                        "text": detail.get("text") or "", "author": detail.get("author") or "",
                        "topic_detail": {"topic_id": match.group(1), "created_at": detail.get("created_at"),
                                         "available": bool(detail)},
                        "comments": comments, "discussion_coverage": coverage,
                        "provenance": {"method": "v2ex_api", "content_role": "community",
                                       "observed_at": coverage.get("collected_at")}}]
            if coverage.get("status") != "complete":
                reasons = list(coverage.get("errors") or []) + list(coverage.get("truncated_by") or [])
                raise CollectionIncomplete(("v2ex:" + ",".join(reasons))[:200] if reasons else "v2ex_incomplete")
            return
    queue = asyncio.Queue(maxsize=1)

    async def on_row(row):
        acknowledged = asyncio.Event()
        await queue.put(("row", row, acknowledged))
        await acknowledged.wait()

    async def produce():
        try:
            _, stats = await crawl_async(topic, options, on_row=on_row)
        except Exception as exc:
            await queue.put(("error", exc, None))
        else:
            await queue.put(("done", stats, None))

    task = asyncio.create_task(produce())
    try:
        while True:
            kind, value, acknowledged = await queue.get()
            if kind == "error":
                raise value
            if kind == "done":
                if any(report.get("status") in {"failed", "blocked", "too_large", "unsupported", "needs_ocr",
                                                  "over_budget", "page_limit", "partial"}
                       for report in value.get("reports", [])):
                    raise CollectionIncomplete("crawl incomplete")
                return
            try:
                yield [value]
            finally:
                acknowledged.set()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
