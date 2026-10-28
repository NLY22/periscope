"""Bounded retrieval of GitHub issue and pull request discussions."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import asyncio
import os
import time

import httpx


API_ROOT = "https://api.github.com"
MAX_COMMENTS = 60
MAX_BODY_CHARS = 30000
MAX_PAGES = 2
PAGE_SIZE = 30
REQUEST_TIMEOUT = 8.0
API_VERSION = "2022-11-28"


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "Periscope-Research/1.0",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _created_at(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, OverflowError):
        return value


def _normalize(item: dict, discussion_type: str) -> dict | None:
    body = item.get("body")
    if not isinstance(body, str) or not body.strip():
        return None
    item_id = item.get("id")
    if item_id is None:
        return None
    node = {
        "id": str(item_id),
        "user": str((item.get("user") or {}).get("login") or ""),
        "text": body.strip(),
        "parent_id": str(item["in_reply_to_id"]) if item.get("in_reply_to_id") is not None else "",
        "url": str(item.get("html_url") or ""),
        "discussion_type": discussion_type,
        "replies": [],
    }
    created = _created_at(item.get("created_at"))
    if created:
        node["created_at"] = created
    if discussion_type == "review":
        if item.get("path"):
            node["path"] = str(item["path"])
        if item.get("line") is not None:
            node["line"] = item["line"]
    return node


async def fetch_issue_detail(
    owner: str,
    repo: str,
    number: int | str,
    *,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict, dict]:
    """Fetch the issue body for an issue or pull request within the given budget."""
    owner, repo, number_text = str(owner).strip(), str(repo).strip(), str(number).strip()
    valid_segment = lambda value: bool(value) and all(
        char.isalnum() or char in "-_." for char in value)
    coverage = {"status": "partial", "truncated_by": [], "errors": []}
    if not (valid_segment(owner) and valid_segment(repo) and number_text.isdigit()):
        coverage["errors"].append("detail:invalid_issue_identity")
        return {}, coverage
    if budget_seconds is not None and budget_seconds <= 0:
        coverage["truncated_by"].append("time_budget")
        return {}, coverage
    endpoint = API_ROOT + "/repos/%s/%s/issues/%s" % (owner, repo, number_text)

    async def collect(active_client: httpx.AsyncClient) -> tuple[dict, dict]:
        try:
            request = active_client.get(endpoint)
            response = await request if budget_seconds is None else await asyncio.wait_for(
                request, timeout=budget_seconds)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("body"), (str, type(None))) or "body" not in payload:
                coverage["errors"].append("detail:invalid_payload")
                return {}, coverage
            body = payload.get("body") or ""
            if len(body) > MAX_BODY_CHARS:
                coverage["truncated_by"].append("body_limit")
            user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
            detail = {"text": body[:MAX_BODY_CHARS], "author": str(user.get("login") or "")}
            if not coverage["truncated_by"]:
                coverage["status"] = "complete"
            return detail, coverage
        except asyncio.TimeoutError:
            coverage["truncated_by"].append("time_budget")
        except httpx.HTTPStatusError as exc:
            coverage["errors"].append("detail:http_%d" % exc.response.status_code)
        except Exception as exc:  # noqa: BLE001 - keep the search result usable
            coverage["errors"].append("detail:%s" % type(exc).__name__)
        return {}, coverage

    if client is not None:
        return await collect(client)
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))
    async with httpx.AsyncClient(headers=_headers(), timeout=timeout,
                                 follow_redirects=True) as active_client:
        return await collect(active_client)


async def _fetch_pages(
    client: httpx.AsyncClient,
    endpoint: str,
    *,
    deadline: float | None,
    max_pages: int,
) -> tuple[list[dict], bool, list[str], int]:
    items: list[dict] = []
    truncated = False
    errors: list[str] = []
    pages = 0
    for page in range(1, max_pages + 1):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return items, True, errors, pages
        params = {"per_page": PAGE_SIZE, "page": page}
        try:
            request = client.get(endpoint, params=params)
            response = await request if remaining is None else await asyncio.wait_for(request, remaining)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                errors.append(endpoint.rsplit("/", 1)[-1] + ":invalid_payload")
                break
            pages += 1
            items.extend(item for item in payload if isinstance(item, dict))
            has_next = "rel=\"next\"" in response.headers.get("Link", "")
            if not has_next:
                break
            if page >= max_pages:
                truncated = True
        except asyncio.TimeoutError:
            return items, True, errors, pages
        except httpx.HTTPStatusError as exc:
            errors.append(endpoint.rsplit("/", 1)[-1] + ":http_%d" % exc.response.status_code)
            break
        except Exception as exc:  # noqa: BLE001 - isolate source failure in coverage
            errors.append(endpoint.rsplit("/", 1)[-1] + ":%s" % type(exc).__name__)
            break
    return items, truncated, errors, pages


def _build_tree(issue_items: list[dict], review_items: list[dict], *, limit: int) -> list[dict]:
    nodes: dict[str, dict] = {}
    parents: dict[str, str] = {}
    for item in issue_items:
        node = _normalize(item, "issue")
        if node:
            nodes[node["id"]] = node
            parents[node["id"]] = ""
    for item in review_items:
        node = _normalize(item, "review")
        if node:
            nodes[node["id"]] = node
            parents[node["id"]] = node["parent_id"]

    ordered = sorted(nodes.items(), key=lambda pair: (
        pair[1].get("created_at", ""), pair[1]["discussion_type"], pair[0]))
    keep_ids = {item_id for item_id, _node in ordered[:limit]}
    roots: list[dict] = []
    for item_id, node in ordered[:limit]:
        parent_id = parents[item_id]
        parent = nodes.get(parent_id)
        if parent_id and parent_id in keep_ids and parent is not node:
            parent["replies"].append(node)
        else:
            roots.append(node)
    for node in nodes.values():
        if not node["replies"]:
            node.pop("replies")
    return roots


async def fetch_discussion(
    owner: str,
    repo: str,
    number: int | str,
    *,
    is_pull_request: bool = False,
    max_comments: int = MAX_COMMENTS,
    max_pages: int = MAX_PAGES,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[dict], dict]:
    """Return issue comments and, for PRs, review comment reply chains."""
    owner, repo = str(owner).strip(), str(repo).strip()
    number_text = str(number).strip()
    max_comments = max(0, int(max_comments))
    max_pages = max(1, int(max_pages))
    valid_segment = lambda value: bool(value) and all(
        char.isalnum() or char in "-_." for char in value)
    valid = valid_segment(owner) and valid_segment(repo) and number_text.isdigit()
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage = {
        "status": "failed" if not valid else "partial",
        "issue_number": number_text if valid else "",
        "is_pull_request": bool(is_pull_request),
        "returned_comments": None,
        "fetched_comments": 0,
        "max_comments": max_comments,
        "pages": {},
        "truncated_by": [],
        "errors": [],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if not valid:
        coverage["errors"].append("invalid_issue_identity")
        return [], coverage
    if max_comments == 0:
        coverage["truncated_by"].append("comment_limit")
        return [], coverage

    endpoints = [
        ("issue", API_ROOT + "/repos/%s/%s/issues/%s/comments" % (owner, repo, number_text)),
    ]
    if is_pull_request:
        endpoints.append(("review", API_ROOT + "/repos/%s/%s/pulls/%s/comments" % (owner, repo, number_text)))

    async def collect(active_client: httpx.AsyncClient):
        tasks = {
            asyncio.create_task(_fetch_pages(active_client, endpoint,
                                              deadline=deadline, max_pages=max_pages)): kind
            for kind, endpoint in endpoints
        }
        wait = None if deadline is None else max(0.0, deadline - time.monotonic())
        done, pending = await asyncio.wait(tasks, timeout=wait)
        deadline_reached = deadline is not None and time.monotonic() >= deadline
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            coverage["truncated_by"].append("time_budget")
        elif deadline_reached and "time_budget" not in coverage["truncated_by"]:
            # A request may resolve on the same event-loop tick as the deadline;
            # its page worker reports only a generic truncation in that case.
            coverage["truncated_by"].append("time_budget")
        results = {"issue": [], "review": []}
        has_more = False
        for task in done:
            kind = tasks[task]
            try:
                items, truncated, errors, pages = task.result()
            except Exception as exc:  # noqa: BLE001
                items, truncated, pages = [], False, 0
                errors = [kind + ":%s" % type(exc).__name__]
            results[kind] = items
            coverage["pages"][kind] = pages
            coverage["errors"].extend(errors)
            has_more = has_more or truncated
        if has_more and "page_limit" not in coverage["truncated_by"]:
            coverage["truncated_by"].append("page_limit")
        raw_count = sum(len(items) for items in results.values())
        coverage["returned_comments"] = raw_count
        comments = _build_tree(results["issue"], results["review"], limit=max_comments)
        normalized_count = sum(1 for _ in _walk(comments))
        coverage["fetched_comments"] = normalized_count
        if raw_count > max_comments:
            coverage["truncated_by"].append("comment_limit")
        if not coverage["truncated_by"] and not coverage["errors"]:
            coverage["status"] = "complete"
        elif normalized_count or coverage["errors"] or coverage["truncated_by"]:
            coverage["status"] = "partial"
        else:
            coverage["status"] = "unknown"
        return comments, coverage

    if client is not None:
        return await collect(client)
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))
    async with httpx.AsyncClient(headers=_headers(), timeout=timeout,
                                 follow_redirects=True) as active_client:
        return await collect(active_client)


def _walk(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _walk(node.get("replies") or [])
