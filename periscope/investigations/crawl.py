"""Bridge the native crawler's page deliveries into investigation batches."""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import aclosing
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from html import unescape

import httpx

from periscope.collection.crawling.engine import crawl_async
from periscope.collection.crawling.engine import CrawlOptions


class CollectionIncomplete(Exception):
    """The source reports that part of the requested crawl was not completed."""


async def stream_hn_search(topic: str, *, budget: float, max_pages: int = 12, depth: int = 2, follow_limit: int = 2):
    """Discover HN stories, persist search cards, then follow a bounded set."""
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
    targets = candidates[:max(0, follow_limit)]
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


async def stream_auto_search(topic: str, *, budget: float, max_pages: int | None = None, depth: int = 2,
                             variants: tuple[str, ...] = (), emit_progress: bool = False):
    """Run bounded source collectors concurrently and persist each emitted batch."""
    if max_pages is not None:
        allocations = [("v2ex", stream_v2ex_search, (max_pages + 1) // 2),
                       ("hn", stream_hn_search, max_pages // 2)]
    else:
        from functools import partial
        from .browser_sources import stream_browser_source
        from .catalog import CATALOG_SOURCES, stream_catalog_source
        from .web_search import stream_public_search, stream_search_api

        allocations = [("v2ex", partial(stream_v2ex_search, follow_limit=3, max_replies=20), 7),
                       ("hn", partial(stream_hn_search, follow_limit=3), 49)]
        for source in CATALOG_SOURCES:
            def catalog_collector(query, *, budget, max_pages, depth, source_key=source):
                return stream_catalog_source(query, source_key, budget=budget, detail_limit=2, max_comments=13, depth=depth)
            allocations.append((source, catalog_collector, 1))
        def public_collector(query, *, budget, max_pages, depth):
            return stream_public_search(query, budget=budget, depth=depth)
        def api_collector(query, *, budget, max_pages, depth):
            return stream_search_api(query, budget=budget, depth=depth)
        allocations.extend((("public_web", public_collector, 1), ("search_api", api_collector, 1)))
        browser_gate = asyncio.Semaphore(1)
        for source in ("xhs_browser", "douyin_browser", "kuaishou_browser"):
            async def browser_collector(query, *, budget, max_pages, depth, source_key=source):
                async with browser_gate:
                    remaining = min(30.0, budget, deadline - time.monotonic())
                    if remaining <= 0:
                        raise CollectionIncomplete(source_key + ":time_budget")
                    async with aclosing(stream_browser_source(query, source_key, budget=remaining, depth=depth)) as batches:
                        async for batch in batches:
                            yield batch
            allocations.append((source, browser_collector, 1))
        priority = ("public_web", "search_api", "v2ex", "zhihu_search", "weibo_search", "bili_search",
                    "csdn_search", "juejin_search", "hn", "github_issues", "stackexchange_search",
                    "arxiv_ai", "openalex", "crossref_works", "xhs_browser", "douyin_browser", "kuaishou_browser")
        allocations.sort(key=lambda item: priority.index(item[0]))
    jobs = [(name, collector, pages, topic, depth, name) for name, collector, pages in allocations]
    if max_pages is None:
        lookup = {name: (collector, pages) for name, collector, pages in allocations}
        source_order = ("public_web", "zhihu_search", "weibo_search", "bili_search", "v2ex", "csdn_search",
                        "juejin_search", "github_issues", "hn", "stackexchange_search", "arxiv_ai", "openalex", "crossref_works")
        seen_queries = {str(topic).strip().casefold()}
        alternative_index = 0
        for raw_query in variants:
            query = str(raw_query).strip()
            if not query or query.casefold() in seen_queries:
                continue
            seen_queries.add(query.casefold())
            for offset in range(2):
                source = source_order[(alternative_index * 2 + offset) % len(source_order)]
                collector, pages = lookup[source]
                jobs.append((source + "#q" + str(alternative_index + 1), collector, pages, query, 0, source))
            alternative_index += 1
            if alternative_index == 7:
                break
    deadline = time.monotonic() + budget
    queue = asyncio.Queue(maxsize=8)
    semaphore = asyncio.Semaphore(8)
    source_locks = {name: asyncio.Lock() for name, _, _ in allocations}
    tasks = []
    incomplete = []
    async def produce(name, collector, pages, query, job_depth, source):
        from .events import SourceProgress
        status, reason, got_rows = "completed", "", False
        try:
            async with source_locks[source]:
                remaining_budget = deadline - time.monotonic()
                if remaining_budget <= 0:
                    raise CollectionIncomplete(name + ":time_budget")
                if emit_progress:
                    await queue.put(("progress", SourceProgress(name, "running"), None))
                async with aclosing(collector(query, budget=remaining_budget, max_pages=pages, depth=job_depth)) as batches:
                    while True:
                        async with semaphore:
                            if deadline <= time.monotonic():
                                raise CollectionIncomplete(name + ":time_budget")
                            try:
                                batch = await anext(batches)
                            except StopAsyncIteration:
                                break
                        if isinstance(batch, SourceProgress):
                            status, reason = batch.status, batch.reason
                            continue
                        got_rows = got_rows or bool(batch)
                        ack = asyncio.Event()
                        await queue.put(("batch", batch, ack))
                        await ack.wait()
        except asyncio.CancelledError:
            raise
        except CollectionIncomplete as exc:
            status, reason = "partial" if got_rows else "failed", str(exc)
            await queue.put(("failure", str(exc), None))
        except Exception as exc:
            status, reason = "failed", type(exc).__name__
            await queue.put(("failure", name + ":" + type(exc).__name__, None))
        finally:
            if not asyncio.current_task().cancelling():
                if emit_progress:
                    await queue.put(("progress", SourceProgress(name, "empty" if status == "completed" and not got_rows else status, reason), None))
                await queue.put(("done", name, None))
    for name, collector, pages, query, job_depth, source in jobs:
        if pages:
            tasks.append(asyncio.create_task(produce(name, collector, pages, query, job_depth, source)))
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
            elif kind == "progress":
                yield value
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


async def stream_v2ex_search(topic: str, *, budget: float, max_pages: int = 12, depth: int = 2,
                             follow_limit: int = 3, max_replies: int = 60):
    """Discover relevant V2EX topics, save search cards, then follow a bounded set."""
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
    for _row, topic_id in candidates[:max(0, follow_limit)]:
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
            batches = (stream_crawl(topic, options) if max_replies == 60 else
                       stream_crawl(topic, options, reply_limit=max_replies))
            async for batch in batches:
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


async def stream_crawl(topic, options, *, reply_limit: int = 60):
    if len(options.seeds) > 1:
        from .browser_sources import identify_browser_url
        from .catalog import identify_community_url

        def platform_seed(seed):
            parsed = urlsplit(seed)
            if parsed.hostname in {"v2ex.com", "www.v2ex.com"} and re.fullmatch(r"/t/\d+/?", parsed.path):
                return True
            if parsed.hostname == "news.ycombinator.com" and parsed.path == "/item":
                ids = parse_qs(parsed.query).get("id", [])
                if len(ids) == 1 and ids[0].isdigit():
                    return True
            return bool(identify_community_url(seed) or identify_browser_url(seed))

        if any(platform_seed(seed) for seed in options.seeds):
            deadline = time.monotonic() + options.budget
            pages_left = options.max_pages
            incomplete = []
            for index, seed in enumerate(options.seeds):
                remaining = deadline - time.monotonic()
                if pages_left <= 0 or remaining <= 0:
                    incomplete.append("seed_page_or_time_limit")
                    break
                seeds_left = len(options.seeds) - index
                pages = max(1, pages_left // seeds_left)
                pages_left -= pages
                bounded = replace(options, seeds=(seed,), max_pages=pages, budget=remaining / seeds_left)
                try:
                    async with aclosing(stream_crawl(topic, bounded, reply_limit=reply_limit)) as batches:
                        async for batch in batches:
                            yield batch
                except CollectionIncomplete as exc:
                    incomplete.append(str(exc))
                except Exception as exc:
                    incomplete.append("seed:" + type(exc).__name__)
            if incomplete:
                raise CollectionIncomplete(("sources:" + ",".join(incomplete))[:200])
            return
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
            max_replies = 0 if options.depth == 0 or options.max_pages == 1 else max(0, reply_limit)
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
        if not options.form_fields:
            from .browser_sources import identify_browser_url, stream_browser_detail
            browser_target = identify_browser_url(options.seeds[0])
            if browser_target:
                started = time.monotonic()
                source, identity = browser_target
                card = {"source": source, "kind": "community", "_origin": "platform",
                        "title": options.seeds[0], "url": options.seeds[0], "text": "",
                        "evidence": {**identity, "content_level": "locator"},
                        "discussion_coverage": {"status": "unsupported" if source == "kuaishou_browser" else "not_attempted"},
                        "provenance": {"method": "user_supplied", "content_role": "community"}}
                yield [card]
                if source == "kuaishou_browser":
                    raise CollectionIncomplete(source + ":discussion_unsupported")
                if options.depth == 0 or options.max_pages == 1 or reply_limit <= 0:
                    raise CollectionIncomplete(source + ":depth_limit")
                remaining = options.budget - (time.monotonic() - started)
                if remaining <= 0:
                    raise CollectionIncomplete(source + ":time_budget")
                async for batch in stream_browser_detail(card, budget=remaining, max_comments=min(60, reply_limit)):
                    yield batch
                return
            from .catalog import identify_community_url, stream_catalog_detail
            found = identify_community_url(options.seeds[0])
            if found:
                started = time.monotonic()
                source, identity = found
                card = {"source": source, "kind": "community", "_origin": "platform",
                        "title": options.seeds[0], "url": options.seeds[0], "text": "",
                        "evidence": {**identity, "content_level": "locator"},
                        "discussion_coverage": {"status": "not_attempted"},
                        "provenance": {"method": "user_supplied", "content_role": "community"}}
                yield [card]
                max_comments = 0 if options.depth == 0 or options.max_pages == 1 else min(60, max(0, reply_limit))
                remaining = options.budget - (time.monotonic() - started)
                if remaining <= 0:
                    raise CollectionIncomplete(source + ":time_budget")
                async for batch in stream_catalog_detail(card, budget=remaining, max_comments=max_comments):
                    yield batch
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
