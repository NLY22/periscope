"""Bounded public CSDN article and comment retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
import asyncio
import re
import time

from bs4 import BeautifulSoup
import httpx

from periscope.collection.transport.parsing import _clean


COMMENT_API = "https://blog.csdn.net/phoenix/web/v1/comment/list/{article_id}"
MAX_COMMENTS = 60
PAGE_SIZE = 20
MAX_COMMENT_PAGES = 3
MAX_REPLY_DEPTH = 4
MAX_BODY_CHARS = 30000
REQUEST_TIMEOUT = 8.0
_ARTICLE_PATH = re.compile(r"^/[^/]+/article/details/(\d+)(?:/)?$")


def _article_parts(article_url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(str(article_url).strip())
    except ValueError:
        return None
    match = _ARTICLE_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "blog.csdn.net" or not match:
        return None
    article_id = match.group(1)
    canonical = "https://blog.csdn.net%s" % parsed.path.rstrip("/")
    return article_id, canonical


def _article_text(body: str) -> str:
    soup = BeautifulSoup(body, "html.parser")
    content = soup.select_one("#content_views") or soup.select_one(".article_content")
    if content is None:
        return ""
    lines = [line.strip() for line in content.get_text("\n").splitlines() if line.strip()]
    return "\n".join(lines)


def _comment(
    raw: dict,
    *,
    article_url: str,
    inherited_parent: str | None,
    remaining: int,
    depth: int = 0,
    seen: set[str] | None = None,
    truncated_by: list[str] | None = None,
) -> tuple[dict | None, int]:
    if depth > MAX_REPLY_DEPTH:
        if truncated_by is not None:
            truncated_by.append("reply_depth_limit")
        return None, 0
    if remaining <= 0 or not isinstance(raw, dict):
        return None, 0
    seen = seen if seen is not None else set()
    info = raw.get("info") if isinstance(raw.get("info"), dict) else raw
    comment_id_value = info.get("commentId") or raw.get("commentId")
    comment_id = str(comment_id_value or "").strip()
    if not comment_id or comment_id in seen:
        return None, 0
    seen.add(comment_id)
    text = _clean(info.get("content"))
    children = raw.get("sub") if isinstance(raw.get("sub"), list) else []
    if not text and not children:
        return None, 0
    parent_value = info.get("parentId")
    parent_id = str(parent_value) if parent_value not in (None, 0, "0", "") else inherited_parent
    node: dict[str, Any] = {
        "id": comment_id,
        "user": str(info.get("userName") or info.get("nickName") or ""),
        "text": text,
        "likes": _number(info.get("digg")),
        "url": article_url,
        "discussion_type": "comment",
    }
    if parent_id:
        node["parent_id"] = parent_id
    if info.get("postTime"):
        node["created_at"] = str(info["postTime"])
    used = 1
    replies = []
    for child in children:
        if used >= remaining:
            if truncated_by is not None:
                truncated_by.append("comment_limit")
            break
        child_node, child_used = _comment(
            child, article_url=article_url,
            inherited_parent=comment_id,
            remaining=remaining - used, depth=depth + 1, seen=seen,
            truncated_by=truncated_by)
        if child_node is not None:
            replies.append(child_node)
            used += child_used
    if replies:
        node["replies"] = replies
    return node, used


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None,
    headers: dict[str, str],
    deadline: float | None,
) -> tuple[httpx.Response | None, str | None]:
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return None, "time_budget"
    try:
        request = client.get(url, params=params, headers=headers, follow_redirects=True)
        response = await request if remaining is None else await asyncio.wait_for(request, remaining)
        response.raise_for_status()
        return response, None
    except asyncio.TimeoutError:
        return None, "time_budget"
    except httpx.HTTPStatusError as exc:
        return None, "http_%d" % exc.response.status_code
    except Exception as exc:  # noqa: BLE001 - keep endpoint failures explicit
        return None, type(exc).__name__


async def fetch_discussion(
    article_url: str,
    *,
    max_comments: int = MAX_COMMENTS,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict, list[dict], dict]:
    """Return full article text, a bounded comment tree, and coverage data."""
    parts = _article_parts(article_url)
    article_id = parts[0] if parts else ""
    canonical_url = parts[1] if parts else ""
    max_comments = max(0, int(max_comments))
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage = {
        "status": "partial" if article_id else "failed",
        "platform": "csdn",
        "article_id": article_id,
        "reported_comments": None,
        "fetched_comments": 0,
        "fetched_roots": 0,
        "fetched_replies": 0,
        "pages_attempted": 0,
        "max_comments": max_comments,
        "truncated_by": [],
        "errors": [],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if not parts:
        coverage["errors"].append("invalid_article_url")
        return {}, [], coverage
    if max_comments == 0:
        coverage["truncated_by"].append("comment_limit")
    if deadline is not None and deadline <= time.monotonic():
        coverage["truncated_by"].append("time_budget")
        return {}, [], coverage

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
        "Referer": canonical_url,
    }
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))
    detail: dict[str, Any] = {"article_id": article_id, "url": canonical_url, "text": ""}

    async def collect(active_client: httpx.AsyncClient) -> tuple[dict, list[dict], dict]:
        article_response, article_error = await _get(
            active_client, canonical_url, params=None, headers=headers, deadline=deadline)
        if article_error == "time_budget":
            coverage["truncated_by"].append("time_budget")
        elif article_error:
            coverage["errors"].append("article:%s" % article_error)
        elif article_response is not None:
            text = _article_text(article_response.text)
            if text:
                detail["text"] = text[:MAX_BODY_CHARS]
                if len(text) > MAX_BODY_CHARS:
                    coverage["truncated_by"].append("article_text_limit")
            else:
                coverage["errors"].append("article:body_missing")

        comments: list[dict] = []
        seen: set[str] = set()
        reported_comments: int | None = None
        reported_pages: int | None = None
        if max_comments > 0:
            for page in range(1, MAX_COMMENT_PAGES + 1):
                if coverage["fetched_comments"] >= max_comments:
                    break
                response, error = await _get(
                    active_client,
                    COMMENT_API.format(article_id=article_id),
                    params={"page": page, "size": min(PAGE_SIZE, max_comments - coverage["fetched_comments"]),
                            "fold": 1, "commentId": 0},
                    headers=headers,
                    deadline=deadline,
                )
                if error == "time_budget":
                    if "time_budget" not in coverage["truncated_by"]:
                        coverage["truncated_by"].append("time_budget")
                    break
                if error:
                    coverage["errors"].append("comments:%s" % error)
                    break
                assert response is not None
                coverage["pages_attempted"] += 1
                try:
                    payload = response.json()
                except Exception:
                    coverage["errors"].append("comments:invalid_json")
                    break
                if not isinstance(payload, dict) or payload.get("code") not in (200, 0, None):
                    code = payload.get("code") if isinstance(payload, dict) else "invalid_payload"
                    coverage["errors"].append("comments:api_%s" % code)
                    break
                data = payload.get("data") or {}
                if not isinstance(data, dict):
                    coverage["errors"].append("comments:invalid_data")
                    break
                count = data.get("count")
                page_count = data.get("pageCount")
                if isinstance(count, int) and count >= 0:
                    reported_comments = max(reported_comments or 0, count)
                    coverage["reported_comments"] = reported_comments
                if isinstance(page_count, int) and page_count >= 0:
                    reported_pages = max(reported_pages or 0, page_count)
                rows = data.get("list") or []
                if not isinstance(rows, list):
                    coverage["errors"].append("comments:invalid_list")
                    break
                if not rows:
                    if reported_comments is not None and coverage["fetched_roots"] < reported_comments:
                        coverage["truncated_by"].append("comment_page_empty")
                    break
                page_size = min(PAGE_SIZE, max_comments - coverage["fetched_comments"])
                for raw in rows:
                    if coverage["fetched_comments"] >= max_comments:
                        coverage["truncated_by"].append("comment_limit")
                        break
                    node, used = _comment(
                        raw, article_url=canonical_url,
                        inherited_parent=None,
                        remaining=max_comments - coverage["fetched_comments"], seen=seen,
                        truncated_by=coverage["truncated_by"])
                    if node is None:
                        continue
                    comments.append(node)
                    coverage["fetched_roots"] += 1
                    coverage["fetched_comments"] += used
                    coverage["fetched_replies"] += used - 1
                if reported_pages is not None and page >= reported_pages:
                    if reported_comments is not None and coverage["fetched_roots"] < reported_comments:
                        coverage["truncated_by"].append("comment_count_mismatch")
                    break
                if reported_pages is None and len(rows) < page_size:
                    if (reported_comments is not None
                            and coverage["fetched_roots"] < reported_comments):
                        coverage["truncated_by"].append("comment_count_mismatch")
                    break
                if page == MAX_COMMENT_PAGES:
                    if reported_pages is None or page < reported_pages:
                        coverage["truncated_by"].append("comment_page_limit")
                    break

        if (coverage["fetched_comments"] >= max_comments and
                (reported_comments is None or coverage["fetched_roots"] < reported_comments)):
            coverage["truncated_by"].append("comment_limit")
        coverage["truncated_by"] = list(dict.fromkeys(coverage["truncated_by"]))
        coverage["status"] = (
            "partial" if coverage["truncated_by"] or coverage["errors"] else "complete")
        return detail, comments, coverage

    if client is not None:
        return await collect(client)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as active_client:
        return await collect(active_client)
