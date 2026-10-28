"""Bounded anonymous article and discussion retrieval from Juejin."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
import asyncio
import re
import time

from bs4 import BeautifulSoup
import httpx


API_ROOT = "https://api.juejin.cn"
ARTICLE_DETAIL = API_ROOT + "/content_api/v1/article/detail"
COMMENT_LIST = API_ROOT + "/interact_api/v1/comment/list"
MAX_COMMENTS = 60
PAGE_SIZE = 20
MAX_COMMENT_PAGES = 3
MAX_BODY_CHARS = 30000
REQUEST_TIMEOUT = 8.0
_ARTICLE_PATH = re.compile(r"^/(?:post|column)/([0-9]{10,25})(?:/)?$")
_CLIENT = {"client_type": 2608, "aid": 2608}


def _article_id(article: str | int) -> tuple[str, str] | None:
    value = str(article).strip()
    if value.isdigit():
        return value, "https://juejin.cn/post/%s" % value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    match = _ARTICLE_PATH.fullmatch(parsed.path)
    if (parsed.scheme != "https" or (parsed.hostname or "").lower() != "juejin.cn"
            or not match):
        return None
    article_id = match.group(1)
    return article_id, "https://juejin.cn/post/%s" % article_id


def _plain_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines()
                      if line.strip())


def _comment(raw: Any, article_url: str, *, remaining: int) -> tuple[dict | None, int, int]:
    if not isinstance(raw, dict) or remaining <= 0:
        return None, 0, 0
    info = raw.get("comment_info") if isinstance(raw.get("comment_info"), dict) else {}
    comment_id = str(raw.get("comment_id") or info.get("comment_id") or "").strip()
    text = str(info.get("comment_content") or "").strip()
    if not comment_id or not text:
        return None, 0, 0
    user_info = raw.get("user_info") if isinstance(raw.get("user_info"), dict) else {}
    node: dict[str, Any] = {
        "id": comment_id,
        "user": str(user_info.get("user_name") or user_info.get("user_id") or ""),
        "text": text,
        "likes": _number(info.get("digg_count")),
        "url": article_url + "#comment-%s" % comment_id,
        "discussion_type": "comment",
    }
    if info.get("ctime"):
        node["created_at"] = info["ctime"]
    used = 1
    replies: list[dict] = []
    raw_replies = info.get("comment_replys")
    if isinstance(raw_replies, list):
        for reply in raw_replies:
            if used >= remaining or not isinstance(reply, dict):
                break
            reply_id = str(reply.get("reply_id") or "").strip()
            reply_text = str(reply.get("reply_content") or "").strip()
            if not reply_id or not reply_text:
                continue
            reply_node: dict[str, Any] = {
                "id": reply_id,
                "user": str(reply.get("user_id") or ""),
                "text": reply_text,
                "likes": _number(reply.get("digg_count")),
                "parent_id": str(reply.get("reply_comment_id") or comment_id),
                "url": article_url + "#comment-%s" % comment_id,
                "discussion_type": "reply",
            }
            if reply.get("ctime"):
                reply_node["created_at"] = reply["ctime"]
            replies.append(reply_node)
            used += 1
    if replies:
        node["replies"] = replies
    return node, used, len(replies)


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    deadline: float | None,
) -> tuple[dict | None, str | None]:
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return None, "time_budget"
    try:
        request = client.post(url, json=payload)
        response = await request if remaining is None else await asyncio.wait_for(request, remaining)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return None, "invalid_payload"
        if data.get("err_no") not in (None, 0):
            return None, "api_%s" % data.get("err_no")
        return data, None
    except asyncio.TimeoutError:
        return None, "time_budget"
    except httpx.HTTPStatusError as exc:
        return None, "http_%d" % exc.response.status_code
    except Exception as exc:  # noqa: BLE001 - retain partial data on source failures
        return None, type(exc).__name__


async def fetch_discussion(
    article: str | int,
    *,
    max_comments: int = MAX_COMMENTS,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict, list[dict], dict]:
    """Fetch article text and a bounded paginated comment/reply tree.

    Juejin's public comment response embeds a limited reply preview per root.
    Any mismatch between that preview and ``reply_count`` is surfaced as partial
    coverage instead of being presented as a complete thread.
    """
    parsed = _article_id(article)
    article_id, article_url = parsed if parsed else ("", "")
    max_comments = max(0, int(max_comments))
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage: dict[str, Any] = {
        "status": "failed" if not parsed else "partial",
        "platform": "juejin",
        "article_id": article_id,
        "reported_comments": None,
        "article_reported_comments": None,
        "fetched_comments": 0,
        "fetched_roots": 0,
        "fetched_replies": 0,
        "pages_attempted": 0,
        "max_comments": max_comments,
        "truncated_by": [],
        "errors": [],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if not parsed:
        coverage["errors"].append("invalid_article_url")
        return {}, [], coverage
    if max_comments == 0:
        coverage["truncated_by"].append("comment_limit")
    if deadline is not None and deadline <= time.monotonic():
        coverage["truncated_by"].append("time_budget")
        return {}, [], coverage

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "Referer": article_url,
    }
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))
    detail: dict[str, Any] = {"article_id": article_id, "url": article_url, "text": ""}

    async def collect(active: httpx.AsyncClient) -> tuple[dict, list[dict], dict]:
        article_payload, article_error = await _post_json(
            active, ARTICLE_DETAIL, {**_CLIENT, "article_id": article_id}, deadline)
        article_info: dict[str, Any] = {}
        if article_error == "time_budget":
            coverage["truncated_by"].append("time_budget")
        elif article_error:
            coverage["errors"].append("article:%s" % article_error)
        elif article_payload is not None:
            data = article_payload.get("data")
            article_info = data.get("article_info") if isinstance(data, dict) else {}
            article_info = article_info if isinstance(article_info, dict) else {}
            author = data.get("author_user_info") if isinstance(data, dict) else {}
            article_text = _plain_text(article_info.get("content"))
            detail.update({
                "title": str(article_info.get("title") or ""),
                "author": str((author or {}).get("user_name") or "") if isinstance(author, dict) else "",
                "text": article_text[:MAX_BODY_CHARS],
            })
            coverage["article_reported_comments"] = _number(article_info.get("comment_count"))
            if len(article_text) > MAX_BODY_CHARS:
                coverage["truncated_by"].append("article_text_limit")
            if not detail["text"]:
                coverage["errors"].append("article:body_missing")

        comments: list[dict] = []
        cursor = "0"
        reported: int | None = None
        has_more = False
        if max_comments > 0:
            for _page in range(MAX_COMMENT_PAGES):
                remaining_nodes = max_comments - coverage["fetched_comments"]
                if remaining_nodes <= 0:
                    break
                payload, error = await _post_json(active, COMMENT_LIST, {
                    **_CLIENT,
                    "item_id": article_id,
                    "item_type": 2,
                    "cursor": cursor,
                    "limit": min(PAGE_SIZE, remaining_nodes),
                }, deadline)
                if error == "time_budget":
                    if "time_budget" not in coverage["truncated_by"]:
                        coverage["truncated_by"].append("time_budget")
                    break
                if error:
                    coverage["errors"].append("comments:%s" % error)
                    break
                assert payload is not None
                coverage["pages_attempted"] += 1
                page_count = payload.get("count")
                if isinstance(page_count, int) and page_count >= 0:
                    reported = page_count if reported is None else max(reported, page_count)
                rows = payload.get("data")
                if not isinstance(rows, list):
                    coverage["errors"].append("comments:invalid_data")
                    break
                has_more = bool(payload.get("has_more"))
                if not rows:
                    if has_more:
                        coverage["truncated_by"].append("empty_page_with_more")
                    break
                for raw in rows:
                    if coverage["fetched_comments"] >= max_comments:
                        coverage["truncated_by"].append("comment_limit")
                        break
                    node, used, reply_count = _comment(
                        raw, article_url, remaining=max_comments - coverage["fetched_comments"])
                    if node is None:
                        continue
                    comments.append(node)
                    coverage["fetched_roots"] += 1
                    coverage["fetched_replies"] += reply_count
                    coverage["fetched_comments"] += used
                    info = raw.get("comment_info") if isinstance(raw, dict) else {}
                    info = info if isinstance(info, dict) else {}
                    if _number(info.get("reply_count")) > reply_count:
                        coverage["truncated_by"].append("reply_preview_only")
                if not has_more:
                    break
                next_cursor = str(payload.get("cursor") or "").strip()
                if not next_cursor or next_cursor == cursor:
                    coverage["truncated_by"].append("cursor_stalled")
                    break
                cursor = next_cursor
                if coverage["fetched_comments"] >= max_comments:
                    coverage["truncated_by"].append("comment_limit")
                    break
            else:
                if has_more:
                    coverage["truncated_by"].append("page_limit")

        coverage["reported_comments"] = reported
        if reported is not None and coverage["fetched_roots"] < reported and not has_more:
            coverage["truncated_by"].append("comment_count_mismatch")
        if reported is not None and coverage["fetched_roots"] < reported and max_comments <= coverage["fetched_comments"]:
            coverage["truncated_by"].append("comment_limit")
        coverage["truncated_by"] = list(dict.fromkeys(coverage["truncated_by"]))
        coverage["status"] = "partial" if (coverage["errors"] or coverage["truncated_by"]) else "complete"
        return detail, comments, coverage

    if client is not None:
        return await collect(client)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as active:
        return await collect(active)
