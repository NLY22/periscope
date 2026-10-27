"""Bounded topic and reply retrieval from V2EX's public legacy API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import asyncio
import time

import httpx


API_ROOT = "https://www.v2ex.com/api"
MAX_REPLIES = 60
REQUEST_TIMEOUT = 8.0


async def _json(client: httpx.AsyncClient, path: str, params: dict,
                deadline: float | None) -> tuple[Any, str | None]:
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return None, "time_budget"
    try:
        request = client.get(API_ROOT + path, params=params)
        response = await request if remaining is None else await asyncio.wait_for(request, remaining)
        response.raise_for_status()
        return response.json(), None
    except asyncio.TimeoutError:
        return None, "time_budget"
    except httpx.HTTPStatusError as exc:
        return None, "http_%d" % exc.response.status_code
    except Exception as exc:  # noqa: BLE001 - report independent endpoint failures
        return None, type(exc).__name__


def _topic(payload: Any, topic_id: str) -> dict | None:
    if not isinstance(payload, list):
        return None
    topic = next((item for item in payload if isinstance(item, dict)
                  and str(item.get("id")) == topic_id), None)
    if topic is None:
        return None
    text = topic.get("content") or topic.get("content_rendered") or ""
    member = topic.get("member") or {}
    node = topic.get("node") or {}
    detail = {
        "title": str(topic.get("title") or ""),
        "text": str(text).strip(),
        "author": str(member.get("username") or ""),
        "url": str(topic.get("url") or "https://www.v2ex.com/t/%s" % topic_id),
        "topic_id": topic_id,
    }
    if node.get("name") or node.get("title"):
        detail["node"] = str(node.get("name") or node.get("title"))
    if isinstance(topic.get("created"), (int, float)):
        detail["created_at"] = int(topic["created"])
    replies = topic.get("replies")
    if isinstance(replies, int) and replies >= 0:
        detail["reported_replies"] = replies
    return detail


def _replies(payload: Any, topic_id: str, limit: int) -> tuple[list[dict], int]:
    if not isinstance(payload, list):
        return [], 0
    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        body = item.get("content") or item.get("content_rendered") or ""
        if not isinstance(body, str) or not body.strip():
            continue
        reply_id = str(item.get("id") or "").strip()
        if not reply_id:
            continue
        member = item.get("member") or {}
        node = {
            "id": reply_id,
            "user": str(member.get("username") or ""),
            "text": body.strip(),
            "parent_id": topic_id,
            "url": "https://www.v2ex.com/t/%s#%s" % (topic_id, reply_id),
            "discussion_type": "reply",
        }
        if isinstance(item.get("created"), (int, float)):
            node["created_at"] = int(item["created"])
        out.append(node)
    return out[:limit], len(out)


async def fetch_discussion(
    topic_id: str | int,
    *,
    max_replies: int = MAX_REPLIES,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict, list[dict], dict]:
    """Fetch one topic's full available body and bounded flat reply list.

    The legacy V1 API exposes replies as a list without reply-to relationships;
    each captured reply is linked to its containing topic, never to a guessed reply.
    """
    topic_id = str(topic_id).strip()
    valid = topic_id.isdigit()
    max_replies = max(0, int(max_replies))
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage = {
        "status": "failed" if not valid else "partial",
        "topic_id": topic_id if valid else "",
        "reported_replies": None,
        "returned_replies": 0,
        "fetched_replies": 0,
        "max_replies": max_replies,
        "truncated_by": [],
        "errors": [],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if not valid:
        coverage["errors"].append("invalid_topic_id")
        return {}, [], coverage
    if max_replies == 0:
        coverage["truncated_by"].append("comment_limit")
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))

    async def collect(active_client: httpx.AsyncClient):
        requests = {
            "topic": asyncio.create_task(_json(active_client, "/topics/show.json",
                                                {"id": topic_id}, deadline)),
        }
        if max_replies:
            requests["replies"] = asyncio.create_task(_json(
                active_client, "/replies/show.json",
                {"topic_id": topic_id, "from": 0, "to": max_replies}, deadline))
        wait = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            done, pending = await asyncio.wait(requests.values(), timeout=wait)
        except asyncio.CancelledError:
            for task in requests.values():
                task.cancel()
            await asyncio.gather(*requests.values(), return_exceptions=True)
            raise
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            coverage["truncated_by"].append("time_budget")

        results = {}
        for key, task in requests.items():
            if task not in done:
                continue
            payload, error = task.result()
            results[key] = payload
            if error == "time_budget":
                if "time_budget" not in coverage["truncated_by"]:
                    coverage["truncated_by"].append(error)
            elif error:
                coverage["errors"].append("%s:%s" % (key, error))

        detail = _topic(results.get("topic"), topic_id) or {}
        if not detail and "topic:missing" not in coverage["errors"]:
            if "topic" in results:
                coverage["errors"].append("topic:missing")
        reported = detail.get("reported_replies")
        if isinstance(reported, int):
            coverage["reported_replies"] = reported
        if "replies" in results and not isinstance(results["replies"], list):
            coverage["errors"].append("replies:invalid_payload")
        comments, returned = _replies(results.get("replies"), topic_id, max_replies)
        coverage["returned_replies"] = returned
        coverage["fetched_replies"] = len(comments)
        if returned > max_replies or (isinstance(reported, int) and reported > len(comments)):
            if "comment_limit" not in coverage["truncated_by"]:
                coverage["truncated_by"].append("comment_limit")
        if not coverage["truncated_by"] and not coverage["errors"]:
            coverage["status"] = "complete"
        elif detail or comments:
            coverage["status"] = "partial"
        elif coverage["errors"] or coverage["truncated_by"]:
            coverage["status"] = "partial"
        else:
            coverage["status"] = "unknown"
        return detail, comments, coverage

    if client is not None:
        return await collect(client)
    async with httpx.AsyncClient(
        headers={"User-Agent": "Periscope-Research/1.0"},
        timeout=timeout, follow_redirects=True,
    ) as active_client:
        return await collect(active_client)
