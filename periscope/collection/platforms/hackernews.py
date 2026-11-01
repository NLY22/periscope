"""Bounded detail retrieval for Hacker News story discussions.

HN's public Firebase API exposes each item's child IDs and each comment's
parent ID. This adapter follows that tree with explicit node, depth,
concurrency, and time limits; a partial tree is returned with coverage data.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from html import unescape
from typing import Any
import asyncio
import re
import time

import httpx


API_ROOT = "https://hacker-news.firebaseio.com/v0"
MAX_COMMENTS = 60
MAX_DEPTH = 4
MAX_CONCURRENCY = 8
REQUEST_TIMEOUT = 8.0
MAX_COMMENT_CHARS = 20_000


def _item_id(value: Any) -> str:
    """Return a valid numeric HN ID, or an empty string."""
    text = str(value or "").strip()
    return text if text.isdigit() else ""


def _comment(item: dict, parent_id: str) -> dict | None:
    text = item.get("text")
    if not isinstance(text, str):
        return None
    text = unescape(re.sub(r"<[^>]*>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    node = {
        "id": str(item.get("id")),
        "user": str(item.get("by") or ""),
        "text": text[:MAX_COMMENT_CHARS],
        "parent_id": parent_id,
        "url": "https://news.ycombinator.com/item?id=%s" % item.get("id"),
        "replies": [],
    }
    if isinstance(item.get("time"), (int, float)):
        node["created_at"] = int(item["time"])
    return node


async def _get_item(client: httpx.AsyncClient, item_id: str) -> dict | None:
    response = await client.get("%s/item/%s.json" % (API_ROOT, item_id))
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


async def fetch_discussion(
    story_id: str,
    *,
    max_comments: int = MAX_COMMENTS,
    max_depth: int = MAX_DEPTH,
    concurrency: int = MAX_CONCURRENCY,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
    story: dict | None = None,
) -> tuple[list[dict], dict]:
    """Fetch an HN story's comment tree and return `(tree, coverage)`.

    `max_depth=1` captures top-level comments only. `max_comments` counts
    attempted comment IDs, including deleted or unavailable items, so a run
    cannot exceed its request bound by encountering invisible comments.
    `budget_seconds=None` disables the aggregate deadline, while each request
    still has the HTTP client's timeout.
    """
    story_id = _item_id(story_id)
    max_comments = max(0, int(max_comments))
    max_depth = max(0, int(max_depth))
    concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage = {
        "story_id": story_id,
        "status": "failed" if not story_id else "partial",
        "reported_descendants": None,
        "fetched_comments": 0,
        "attempted_ids": 0,
        "max_depth_seen": 0,
        "max_depth": max_depth,
        "max_comments": max_comments,
        "truncated_by": [],
        "errors": [],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if not story_id:
        coverage["errors"].append("invalid_story_id")
        return [], coverage

    async def collect_with(active_client: httpx.AsyncClient) -> tuple[list[dict], dict]:
        def remaining() -> float | None:
            return None if deadline is None else max(0.0, deadline - time.monotonic())

        try:
            timeout = remaining()
            if timeout == 0:
                coverage["truncated_by"].append("time_budget")
                return [], coverage
            story_request = _get_item(active_client, story_id) if story is None else None
            fetched_story = (story if story is not None else
                             await story_request if timeout is None else
                             await asyncio.wait_for(story_request, timeout=timeout))
        except asyncio.TimeoutError:
            coverage["truncated_by"].append("time_budget")
            return [], coverage
        except Exception as exc:  # noqa: BLE001 - a source failure is reported per story
            coverage["errors"].append("story:%s" % type(exc).__name__)
            return [], coverage

        if not fetched_story:
            coverage["errors"].append("story_missing")
            return [], coverage
        descendants = fetched_story.get("descendants")
        if isinstance(descendants, int) and descendants >= 0:
            coverage["reported_descendants"] = descendants

        todo = deque()
        queued: set[str] = set()
        order: dict[str, int] = {}
        next_order = 0
        for child in fetched_story.get("kids") or []:
            child_id = _item_id(child)
            if child_id and child_id not in queued:
                todo.append((child_id, story_id, 1))
                queued.add(child_id)
                order[child_id] = next_order
                next_order += 1
        if not todo and coverage["reported_descendants"] == 0:
            coverage["status"] = "complete"
            return [], coverage
        if max_depth == 0 and todo:
            coverage["truncated_by"].append("depth_limit")
            coverage["status"] = "partial"
            return [], coverage

        attempted: set[str] = set()
        nodes: dict[str, dict] = {}
        parents: dict[str, str] = {}
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_one(item_id: str):
            async with semaphore:
                return await _get_item(active_client, item_id)

        while todo and len(attempted) < max_comments:
            room = max_comments - len(attempted)
            batch = []
            while todo and len(batch) < min(concurrency, room):
                item_id, parent_id, depth = todo.popleft()
                if item_id in attempted:
                    continue
                attempted.add(item_id)
                batch.append((item_id, parent_id, depth))
            if not batch:
                continue

            if remaining() == 0:
                coverage["truncated_by"].append("time_budget")
                break
            tasks = {asyncio.create_task(fetch_one(item_id)): (item_id, parent_id, depth)
                     for item_id, parent_id, depth in batch}
            timeout = remaining()
            try:
                done, pending = await asyncio.wait(tasks, timeout=timeout)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
                coverage["truncated_by"].append("time_budget")

            for task in done:
                item_id, parent_id, depth = tasks[task]
                try:
                    item = task.result()
                except Exception as exc:  # noqa: BLE001
                    coverage["errors"].append("item:%s:%s" % (item_id, type(exc).__name__))
                    continue
                if not isinstance(item, dict):
                    coverage["errors"].append("item:%s:missing" % item_id)
                    continue
                parents[item_id] = parent_id
                if not item.get("deleted") and not item.get("dead") and item.get("type") == "comment":
                    node = _comment(item, parent_id)
                    if node:
                        nodes[item_id] = node
                        coverage["max_depth_seen"] = max(coverage["max_depth_seen"], depth)
                children = item.get("kids") or []
                if children and depth < max_depth:
                    for child in children:
                        child_id = _item_id(child)
                        if child_id and child_id not in queued:
                            todo.append((child_id, item_id, depth + 1))
                            queued.add(child_id)
                            order[child_id] = next_order
                            next_order += 1
                elif children and depth >= max_depth:
                    if "depth_limit" not in coverage["truncated_by"]:
                        coverage["truncated_by"].append("depth_limit")

            if pending:
                break

        if todo and len(attempted) >= max_comments:
            coverage["truncated_by"].append("comment_limit")
        coverage["attempted_ids"] = len(attempted)
        coverage["fetched_comments"] = len(nodes)

        # Keep Firebase's ranked kid order. The explicit parent_id survives even
        # when a deleted or missing parent cannot be represented as a tree node.
        roots: list[dict] = []
        for item_id, node in sorted(nodes.items(), key=lambda pair: order[pair[0]]):
            parent = nodes.get(parents.get(item_id, ""))
            if parent is not None:
                parent["replies"].append(node)
            else:
                roots.append(node)
        for node in nodes.values():
            if not node["replies"]:
                node.pop("replies")

        reported = coverage["reported_descendants"]
        if not coverage["truncated_by"] and not coverage["errors"] and reported is not None \
                and coverage["attempted_ids"] >= reported:
            coverage["status"] = "complete"
        elif nodes:
            coverage["status"] = "partial"
        elif coverage["errors"] or coverage["truncated_by"]:
            coverage["status"] = "partial"
        else:
            coverage["status"] = "unknown"
        return roots, coverage

    if client is not None:
        return await collect_with(client)
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as own_client:
        return await collect_with(own_client)


async def enrich_many(
    story_ids: list[str],
    *,
    concurrency: int = 4,
    budget_seconds: float | None = 30.0,
) -> dict[str, tuple[list[dict], dict]]:
    """Enrich multiple stories under one shared time budget and client."""
    started = time.monotonic()
    deadline = started + budget_seconds if budget_seconds is not None else None
    results: dict[str, tuple[list[dict], dict]] = {}
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 4)))
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def one(raw_id: str):
            story_id = _item_id(raw_id)
            async with semaphore:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    results[story_id or str(raw_id)] = ([], {
                        "story_id": story_id, "status": "partial", "reported_descendants": None,
                        "fetched_comments": 0, "attempted_ids": 0, "max_depth_seen": 0,
                        "max_depth": MAX_DEPTH, "max_comments": MAX_COMMENTS,
                        "truncated_by": ["time_budget"], "errors": [],
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                    })
                    return
                result = await fetch_discussion(
                    story_id, concurrency=MAX_CONCURRENCY,
                    budget_seconds=remaining, client=client)
                results[story_id] = result

        await asyncio.gather(*(one(story_id) for story_id in dict.fromkeys(story_ids)))
    return results
