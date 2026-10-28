"""Bridge the native crawler's page deliveries into investigation batches."""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import parse_qs, urlsplit
from html import unescape

import httpx

from periscope.collection.crawling.engine import crawl_async
from periscope.collection.crawling.engine import CrawlOptions


class CollectionIncomplete(Exception):
    """The source reports that part of the requested crawl was not completed."""


async def stream_hn_search(topic: str, *, budget: float, max_pages: int = 12, depth: int = 2):
    """Discover HN stories, persist search cards, then follow at most two."""
    from periscope.collection.strategies.focused import collect_topic_async

    deadline = time.monotonic() + budget
    rows, stats = await collect_topic_async(topic, sources=["hn_ai"], pages=1,
                                            timeout=min(15, budget), budget=min(30, budget))
    candidates, seen, incomplete = [], set(), []
    for row in rows:
        story_id = str((row.get("ids") or {}).get("objectID") or "")
        if not story_id.isdigit() or story_id in seen:
            continue
        seen.add(story_id)
        url = "https://news.ycombinator.com/item?id=" + story_id
        card = {**row, "source": "hn_ai", "kind": "community", "_origin": "platform", "url": url,
                "topic_detail": {"story_id": story_id, "content_level": "search_result", "external_url": row.get("url") or ""},
                "discussion_coverage": {"status": "not_attempted"},
                "provenance": {"method": "hn_search", "content_role": "community"}}
        candidates.append(story_id)
        if len(candidates) > 20:
            incomplete.append("hn:search_result_limit")
            break
        yield [card]
    remaining_pages = max(0, max_pages - 1) if depth else 0
    followed = 0
    targets = candidates[:2]
    for index, story_id in enumerate(targets):
        remaining = deadline - time.monotonic()
        if remaining_pages <= 0 or remaining <= 0:
            break
        pages = max(1, remaining_pages // (len(targets) - index))
        remaining_pages -= pages
        followed += 1
        try:
            options = CrawlOptions(("https://news.ycombinator.com/item?id=" + story_id,),
                                   max_pages=pages, depth=depth, budget=remaining)
            async for batch in stream_hn_story(story_id, options):
                yield batch
        except CollectionIncomplete as exc:
            incomplete.append(str(exc))
    if len(candidates) > followed:
        incomplete.append("hn:depth_limit" if not depth else "hn:page_or_candidate_limit")
    if stats.get("failed"):
        incomplete.append("hn:search_unavailable")
    if stats.get("over_budget"):
        incomplete.append("hn:search_time_budget")
    if incomplete:
        raise CollectionIncomplete(",".join(incomplete)[:200])


async def stream_auto_search(topic: str, *, budget: float, max_pages: int = 12, depth: int = 2):
    """Run bounded source collectors concurrently and persist each emitted batch."""
    allocations = [("v2ex", stream_v2ex_search, (max_pages + 1) // 2),
                   ("hn", stream_hn_search, max_pages // 2)]
    queue = asyncio.Queue(maxsize=2)
    tasks = []
    incomplete = []
    async def produce(name, collector, pages):
        try:
            async for batch in collector(topic, budget=budget, max_pages=pages, depth=depth):
                ack = asyncio.Event()
                await queue.put(("batch", batch, ack))
                await ack.wait()
        except asyncio.CancelledError:
            raise
        except CollectionIncomplete as exc:
            await queue.put(("failure", str(exc), None))
        except Exception as exc:
            await queue.put(("failure", name + ":" + type(exc).__name__, None))
        finally:
            if not asyncio.current_task().cancelling():
                await queue.put(("done", name, None))
    for name, collector, pages in allocations:
        if pages:
            tasks.append(asyncio.create_task(produce(name, collector, pages)))
        else:
            incomplete.append(name + ":page_limit")
    remaining = len(tasks)
    try:
        while remaining:
            kind, value, ack = await queue.get()
            if kind == "done":
                remaining -= 1
            elif kind == "failure":
                incomplete.append(value)
            else:
                try:
                    yield value
                finally:
                    ack.set()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if incomplete:
        raise CollectionIncomplete(("sources:" + ",".join(incomplete))[:200])


async def stream_v2ex_search(topic: str, *, budget: float, max_pages: int = 12, depth: int = 2):
    """Discover relevant V2EX topics, save search cards, then follow up to three."""
    from periscope.collection.strategies.focused import collect_topic_async

    deadline = time.monotonic() + budget
    rows, stats = await collect_topic_async(topic, sources=["v2ex_search"], pages=1,
                                            timeout=min(15, budget), budget=min(30, budget))
    candidates = []
    seen = set()
    for row in rows:
        url = urlsplit(row.get("url") or "")
        match = re.fullmatch(r"/t/(\d+)/?", url.path)
        if url.hostname not in {"v2ex.com", "www.v2ex.com"} or not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        candidates.append((row, match.group(1)))
    incomplete = []
    for row, topic_id in candidates:
        if time.monotonic() >= deadline:
            incomplete.append("time_budget")
            break
        seed = "https://www.v2ex.com/t/" + topic_id
        card = {**row, "url": seed, "source": "v2ex_search", "kind": "community", "_origin": "platform",
                "topic_detail": {"topic_id": topic_id, "available": True, "content_level": "search_result",
                                 "created_at": row.get("date") or None},
                "discussion_coverage": {"status": "not_attempted"},
                "provenance": {"method": "v2ex_search", "content_role": "community"}}
        yield [card]
    remaining_pages = max(0, max_pages - 1) if depth > 0 else 0
    followed = 0
    for _row, topic_id in candidates[:3]:
        if remaining_pages == 0:
            break
        seed = "https://www.v2ex.com/t/" + topic_id
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            incomplete.append("time_budget")
            break
        try:
            pages = min(2, remaining_pages)
            remaining_pages -= pages
            followed += 1
            options = CrawlOptions((seed,), max_pages=pages, depth=1, budget=remaining)
            async for batch in stream_crawl(topic, options):
                yield batch
        except CollectionIncomplete as exc:
            incomplete.append(str(exc))
    if len(candidates) > followed:
        incomplete.append("depth_limit" if depth == 0 else "page_limit" if remaining_pages == 0 else "candidate_limit")
    if stats.get("over_budget"):
        incomplete.append("search_time_budget")
    if stats.get("failed"):
        errors = [str(item[3]) for item in stats.get("per", []) if len(item) >= 4 and item[3]]
        codes = [match.group(1) for error in errors if (match := re.search(r"\bHTTP\s+(\d{3})\b", error))]
        incomplete.append("search_http_" + codes[0] if codes else "search_unavailable")
    if incomplete:
        raise CollectionIncomplete(("v2ex:" + ",".join(incomplete))[:200])


async def stream_crawl(topic, options):
    if len(options.seeds) == 1:
        url = urlsplit(options.seeds[0])
        hn_id = parse_qs(url.query).get("id", []) if url.hostname == "news.ycombinator.com" and url.path == "/item" else []
        if len(hn_id) == 1 and hn_id[0].isdigit():
            async for batch in stream_hn_story(hn_id[0], options):
                yield batch
            return
        match = re.fullmatch(r"/t/(\d+)/?", url.path)
        if url.hostname in {"v2ex.com", "www.v2ex.com"} and match:
            from periscope.collection.platforms.v2ex import fetch_discussion
            max_replies = 0 if options.depth == 0 or options.max_pages == 1 else 60
            detail, comments, coverage = await fetch_discussion(match.group(1), budget_seconds=options.budget,
                                                                 max_replies=max_replies)
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


async def stream_hn_story(story_id: str, options):
    """Save the story before following a bounded number of discussion nodes."""
    from periscope.collection.platforms.hackernews import _get_item, fetch_discussion
    deadline = time.monotonic() + options.budget
    async with httpx.AsyncClient(timeout=min(8, options.budget), follow_redirects=True) as client:
        try:
            async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                story = await _get_item(client, story_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CollectionIncomplete("hn:story:" + type(exc).__name__) from exc
        if not story or story.get("type") != "story" or str(story.get("id")) != story_id:
            raise CollectionIncomplete("hn:story_missing")
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", str(story.get("text") or "")))).strip()
        url = "https://news.ycombinator.com/item?id=" + story_id
        row = {"source": "hn_ai", "kind": "community", "_origin": "platform", "url": url,
               "title": str(story.get("title") or url), "text": text, "author": str(story.get("by") or ""),
               "date": str(story.get("time") or ""), "ids": {"objectID": story_id},
               "topic_detail": {"story_id": story_id, "content_level": "full", "external_url": story.get("url") or ""},
               "discussion_coverage": {"status": "not_attempted"},
               "provenance": {"method": "hn_api", "content_role": "community"}}
        yield [row]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CollectionIncomplete("hn:time_budget")
        comments, coverage = await fetch_discussion(story_id, story=story, client=client,
            max_comments=min(60, max(0, options.max_pages - 1)), max_depth=min(4, options.depth), budget_seconds=remaining)
        yield [{**row, "comments": comments, "discussion_coverage": coverage}]
        if coverage.get("status") != "complete":
            reasons = list(coverage.get("errors") or []) + list(coverage.get("truncated_by") or [])
            raise CollectionIncomplete(("hn:" + ",".join(reasons))[:200] if reasons else "hn:incomplete")
