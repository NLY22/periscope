"""Bridge the native crawler's page deliveries into investigation batches."""
from __future__ import annotations

import asyncio

from periscope.collection.crawling.engine import crawl_async


class CollectionIncomplete(Exception):
    """The source reports that part of the requested crawl was not completed."""


async def stream_crawl(topic, options):
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
