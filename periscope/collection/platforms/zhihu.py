"""Bounded answer and comment retrieval from Zhihu's public API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
import asyncio
import re
import time

from bs4 import BeautifulSoup
import httpx

from periscope.collection.transport.client import _load_cookies


API_ROOT = "https://www.zhihu.com/api/v4"
MAX_COMMENTS = 60
PAGE_SIZE = 20
MAX_COMMENT_PAGES = 4
MAX_REPLY_THREADS = 5
MAX_REPLY_PAGES = 3
MAX_REPLY_RESERVE = 15
MAX_BODY_CHARS = 30000
REQUEST_TIMEOUT = 8.0
_ANSWER_PATH = re.compile(r"^/question/\d+/answer/(\d+)/?$|^/answer/(\d+)/?$")


def _answer_id(answer_url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(str(answer_url).strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
            "zhihu.com", "www.zhihu.com"}:
        return None
    match = _ANSWER_PATH.fullmatch(parsed.path)
    if not match:
        return None
    answer_id = next((group for group in match.groups() if group), "")
    return (answer_id, "https://www.zhihu.com/answer/%s" % answer_id) if answer_id else None


def _plain_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines()
                      if line.strip())


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _error_name(payload: dict | None, status_code: int | None = None) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        if error.get("need_login") or error.get("code") in (40353, 10003):
            return "login_required"
        if error.get("code"):
            return "api_%s" % error["code"]
    return "http_%s" % status_code if status_code else "invalid_payload"


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None,
    headers: dict[str, str],
    deadline: float | None,
) -> tuple[dict | None, str | None]:
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return None, "time_budget"
    try:
        request = client.get(url, params=params, headers=headers, follow_redirects=True)
        response = await request if remaining is None else await asyncio.wait_for(request, remaining)
        try:
            payload = response.json()
        except Exception:
            payload = None
        if response.status_code >= 400:
            return None, _error_name(payload, response.status_code)
        if not isinstance(payload, dict):
            return None, "invalid_payload"
        if isinstance(payload.get("error"), dict):
            return None, _error_name(payload)
        return payload, None
    except asyncio.TimeoutError:
        return None, "time_budget"
    except httpx.HTTPStatusError as exc:
        return None, _error_name(None, exc.response.status_code)
    except Exception as exc:  # noqa: BLE001 - preserve other sources on endpoint changes
        return None, type(exc).__name__


def _normalize_comment(raw: Any, answer_url: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    comment_id = str(raw.get("id") or "").strip()
    text = _plain_text(raw.get("content"))
    if not comment_id or not text:
        return None
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    parent = raw.get("reply_to_comment")
    parent_id = str(parent.get("id") or "") if isinstance(parent, dict) else ""
    node: dict[str, Any] = {
        "id": comment_id,
        "user": str(author.get("name") or ""),
        "text": text,
        "likes": _number(raw.get("vote_count")),
        "url": str(raw.get("url") or answer_url + "#comment-%s" % comment_id),
        "discussion_type": "reply" if parent_id else "comment",
    }
    if parent_id:
        node["parent_id"] = parent_id
    if raw.get("created_time"):
        node["created_at"] = raw["created_time"]
    return node


def _tree(nodes: list[dict]) -> list[dict]:
    by_id = {node["id"]: node for node in nodes}
    roots: list[dict] = []
    for node in nodes:
        node.pop("replies", None)
    for node in nodes:
        parent_id = node.get("parent_id")
        parent = by_id.get(parent_id) if parent_id else None
        if parent is not None and parent is not node:
            parent.setdefault("replies", []).append(node)
        else:
            roots.append(node)
    return roots


async def fetch_discussion(
    answer_url: str,
    *,
    max_comments: int = MAX_COMMENTS,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict, list[dict], dict]:
    """Fetch one answer and bounded comments, reusing an exported cookie if present.

    Search and comments can work anonymously. Zhihu may require the existing
    optional `zhihu` cookie file for answer bodies and expanded reply access; no
    interactive browser or new login flow is started by this adapter.
    """
    parsed = _answer_id(answer_url)
    answer_id, canonical_url = parsed if parsed else ("", "")
    max_comments = max(0, int(max_comments))
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage: dict[str, Any] = {
        "status": "failed" if not parsed else "partial",
        "platform": "zhihu",
        "answer_id": answer_id,
        "reported_comments": None,
        "reported_replies": 0,
        "fetched_comments": 0,
        "fetched_roots": 0,
        "fetched_replies": 0,
        "pages_attempted": 0,
        "reply_pages_attempted": 0,
        "reply_threads_attempted": 0,
        "max_comments": max_comments,
        "truncated_by": [],
        "errors": [],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if not parsed:
        coverage["errors"].append("invalid_answer_url")
        return {}, [], coverage
    if max_comments == 0:
        coverage["truncated_by"].append("comment_limit")
    if deadline is not None and deadline <= time.monotonic():
        coverage["truncated_by"].append("time_budget")
        return {}, [], coverage

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "Referer": canonical_url,
        "x-requested-with": "fetch",
    }
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))
    detail: dict[str, Any] = {"answer_id": answer_id, "url": canonical_url, "text": ""}

    async def collect(active: httpx.AsyncClient) -> tuple[dict, list[dict], dict]:
        answer_payload, answer_error = await _get_json(
            active, "%s/answers/%s" % (API_ROOT, answer_id),
            params={"include": "data[*].content,data[*].comment_count,data[*].author"},
            headers=headers, deadline=deadline)
        if answer_error == "time_budget":
            coverage["truncated_by"].append("time_budget")
        elif answer_error:
            coverage["errors"].append("answer:%s" % answer_error)
        elif answer_payload is not None:
            answer = answer_payload.get("data")
            answer = answer if isinstance(answer, dict) else answer_payload
            author = answer.get("author") if isinstance(answer.get("author"), dict) else {}
            answer_text = _plain_text(answer.get("content"))
            detail.update({
                "text": answer_text[:MAX_BODY_CHARS],
                "author": str(author.get("name") or ""),
            })
            coverage["answer_reported_comments"] = _number(answer.get("comment_count"))
            if len(answer_text) > MAX_BODY_CHARS:
                coverage["truncated_by"].append("answer_text_limit")
            if not detail["text"]:
                coverage["errors"].append("answer:body_missing")

        raw_comments: list[dict] = []
        seen_comment_ids: set[str] = set()
        cursor = 0
        reported: int | None = None
        page_has_more = False

        async def fetch_root_page(limit: int) -> bool:
            nonlocal cursor, reported, page_has_more
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                coverage["truncated_by"].append("time_budget")
                return False
            payload, error = await _get_json(
                active, "%s/answers/%s/comments" % (API_ROOT, answer_id),
                params={
                    "limit": min(PAGE_SIZE, limit),
                    "offset": cursor,
                    "include": "data[*].content,data[*].author.name,data[*].reply_to_comment,paging.totals",
                }, headers=headers, deadline=deadline)
            if error == "time_budget":
                coverage["truncated_by"].append("time_budget")
                return False
            if error:
                coverage["errors"].append("comments:%s" % error)
                return False
            assert payload is not None
            coverage["pages_attempted"] += 1
            rows = payload.get("data")
            if not isinstance(rows, list):
                coverage["errors"].append("comments:invalid_data")
                return False
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            total = paging.get("totals")
            if isinstance(total, int) and total >= 0:
                reported = total if reported is None else max(reported, total)
            page_has_more = not bool(paging.get("is_end", len(rows) < limit))
            if not rows and page_has_more:
                coverage["truncated_by"].append("empty_page_with_more")
            for raw in rows:
                node = _normalize_comment(raw, canonical_url)
                if not node or node["id"] in seen_comment_ids:
                    continue
                if len(raw_comments) >= max_comments:
                    coverage["truncated_by"].append("comment_limit")
                    break
                seen_comment_ids.add(node["id"])
                raw_comments.append(node)
            cursor += len(rows)
            return bool(rows)

        if max_comments > 0:
            reply_reserve = min(MAX_REPLY_RESERVE, max_comments // 4)
            root_target = max_comments - reply_reserve
            while (coverage["pages_attempted"] < MAX_COMMENT_PAGES
                   and len(raw_comments) < root_target):
                if coverage["pages_attempted"] and not page_has_more:
                    break
                if not await fetch_root_page(root_target - len(raw_comments)):
                    break

            reply_roots = [node for node in raw_comments if not node.get("parent_id")]
            reported_replies_by_parent: dict[str, int] = {}
            for root in reply_roots[:MAX_REPLY_THREADS]:
                root_id = root["id"]
                if len(raw_comments) >= max_comments:
                    break
                coverage["reply_threads_attempted"] += 1
                child_offset = 0
                child_has_more = False
                for reply_page in range(MAX_REPLY_PAGES):
                    remaining_nodes = max_comments - len(raw_comments)
                    if remaining_nodes <= 0:
                        coverage["truncated_by"].append("comment_limit")
                        break
                    payload, error = await _get_json(
                        active, "%s/comments/%s/child_comments" % (API_ROOT, root_id),
                        params={
                            "limit": min(PAGE_SIZE, remaining_nodes),
                            "offset": child_offset,
                            "order": "normal",
                            "include": "data[*].content,data[*].author.name,data[*].reply_to_comment,paging.totals",
                        }, headers=headers, deadline=deadline)
                    if error == "time_budget":
                        coverage["truncated_by"].append("time_budget")
                        break
                    if error:
                        coverage["errors"].append("replies:%s" % error)
                        break
                    assert payload is not None
                    coverage["reply_pages_attempted"] += 1
                    child_rows = payload.get("data")
                    if not isinstance(child_rows, list):
                        coverage["errors"].append("replies:invalid_data")
                        break
                    paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
                    total = paging.get("totals")
                    if isinstance(total, int) and total >= 0:
                        reported_replies_by_parent[root_id] = max(
                            reported_replies_by_parent.get(root_id, 0), total)
                    child_has_more = not bool(paging.get("is_end", len(child_rows) < PAGE_SIZE))
                    for raw in child_rows:
                        node = _normalize_comment(raw, canonical_url)
                        if not node:
                            continue
                        if not node.get("parent_id"):
                            node["parent_id"] = root_id
                            node["discussion_type"] = "reply"
                        if node["id"] in seen_comment_ids:
                            continue
                        if len(raw_comments) >= max_comments:
                            coverage["truncated_by"].append("comment_limit")
                            break
                        seen_comment_ids.add(node["id"])
                        raw_comments.append(node)
                    if not child_rows and child_has_more:
                        coverage["truncated_by"].append("empty_page_with_more")
                        break
                    if not child_has_more or not child_rows:
                        break
                    child_offset += len(child_rows)
                else:
                    if child_has_more:
                        coverage["truncated_by"].append("reply_page_limit")

            coverage["reported_replies"] = sum(reported_replies_by_parent.values())
            fetched_reply_ids_by_parent: dict[str, set[str]] = {}
            for node in raw_comments:
                parent_id = str(node.get("parent_id") or "")
                if parent_id:
                    fetched_reply_ids_by_parent.setdefault(parent_id, set()).add(node["id"])
            if any(count > len(fetched_reply_ids_by_parent.get(parent_id, set()))
                   for parent_id, count in reported_replies_by_parent.items()):
                coverage["truncated_by"].append("reply_count_mismatch")

            while (len(raw_comments) < max_comments and page_has_more
                   and coverage["pages_attempted"] < MAX_COMMENT_PAGES):
                if not await fetch_root_page(max_comments - len(raw_comments)):
                    break
            if len(raw_comments) >= max_comments and page_has_more:
                coverage["truncated_by"].append("comment_limit")
            elif page_has_more and coverage["pages_attempted"] >= MAX_COMMENT_PAGES:
                coverage["truncated_by"].append("page_limit")

        coverage["reported_comments"] = reported
        coverage["fetched_comments"] = len(raw_comments)
        coverage["fetched_roots"] = sum(1 for row in raw_comments if not row.get("parent_id"))
        coverage["fetched_replies"] = len(raw_comments) - coverage["fetched_roots"]
        if reported is not None and len(raw_comments) < reported and not page_has_more:
            coverage["truncated_by"].append("comment_count_mismatch")
        coverage["truncated_by"] = list(dict.fromkeys(coverage["truncated_by"]))
        coverage["status"] = "partial" if (coverage["errors"] or coverage["truncated_by"]) else "complete"
        return detail, _tree(raw_comments), coverage

    if client is not None:
        return await collect(client)
    async with httpx.AsyncClient(headers=headers, cookies=_load_cookies("zhihu"),
                                 timeout=timeout, follow_redirects=True) as active:
        return await collect(active)
