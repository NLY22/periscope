"""Bounded status and comment-thread retrieval from Weibo."""

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


API_ROOT = "https://weibo.com/ajax/statuses"
STATUS_API = API_ROOT + "/show"
COMMENTS_API = API_ROOT + "/buildComments"
MAX_COMMENTS = 60
PAGE_SIZE = 20
MAX_COMMENT_PAGES = 3
MAX_REPLY_THREADS = 5
MAX_REPLY_PAGES = 3
MAX_REPLY_RESERVE = 15
MAX_REPLY_DEPTH = 4
MAX_BODY_CHARS = 10000
REQUEST_TIMEOUT = 8.0
_STATUS_PATH = re.compile(r"^/(?:detail/|\d+/)?([A-Za-z0-9]+)(?:/)?$")


def _post_parts(post_url: str, post_id: str | int | None) -> tuple[str, str] | None:
    selected_id = str(post_id or "").strip()
    try:
        parsed = urlsplit(str(post_url).strip())
    except ValueError:
        return None
    if (parsed.scheme != "https" or (parsed.hostname or "").lower() not in
            {"weibo.com", "www.weibo.com"}):
        return None
    match = _STATUS_PATH.fullmatch(parsed.path)
    if not selected_id:
        selected_id = match.group(1) if match else ""
    if not selected_id or not re.fullmatch(r"[A-Za-z0-9]+", selected_id):
        return None
    display_id = match.group(1) if match else selected_id
    return selected_id, "https://weibo.com/detail/%s" % display_id


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


def _login_error(payload: Any, *, status_code: int | None = None, content_type: str = "") -> str:
    if "html" in content_type.lower():
        return "login_required"
    if isinstance(payload, dict):
        if payload.get("ok") in (0, -100):
            message = str(payload.get("msg") or payload.get("message") or "").lower()
            visitor_url = str(payload.get("url") or "").lower()
            if (payload.get("ok") == -100 or "visitor" in visitor_url
                    or any(token in message for token in ("login", "visitor", "登录", "未登录"))):
                return "login_required"
        if payload.get("errno") in (100001, 100002) or payload.get("code") in (403, 401):
            return "login_required"
    return "http_%d" % status_code if status_code else "invalid_payload"


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict,
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
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            return None, _login_error(payload, status_code=response.status_code,
                                      content_type=content_type)
        if not isinstance(payload, dict):
            return None, _login_error(payload, content_type=content_type)
        if payload.get("ok") != 1:
            return None, _login_error(payload, content_type=content_type)
        return payload, None
    except asyncio.TimeoutError:
        return None, "time_budget"
    except httpx.HTTPStatusError as exc:
        return None, _login_error(None, status_code=exc.response.status_code,
                                  content_type=exc.response.headers.get("content-type", ""))
    except Exception as exc:  # noqa: BLE001 - isolate platform failures
        return None, type(exc).__name__


def _comment_tree(
    raw: Any,
    *,
    post_url: str,
    remaining: int,
    inherited_parent: str = "",
    depth: int = 0,
    seen_ids: set[str] | None = None,
    coverage: dict,
) -> tuple[dict | None, int, int]:
    if not isinstance(raw, dict) or remaining < 0:
        return None, 0, 0
    if depth > MAX_REPLY_DEPTH:
        coverage["truncated_by"].append("reply_depth_limit")
        return None, 0, 0
    comment_id = str(raw.get("idstr") or raw.get("id") or "").strip()
    text = _plain_text(raw.get("text_raw") or raw.get("text") or raw.get("content"))
    if not comment_id or not text:
        return None, 0, 0
    already_seen = seen_ids is not None and comment_id in seen_ids
    if remaining <= 0 and not already_seen:
        coverage["truncated_by"].append("comment_limit")
        return None, 0, 0
    if seen_ids is not None:
        seen_ids.add(comment_id)
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    explicit_parent_value = raw.get("reply_comment_id") or raw.get("rootid")
    parent_value = explicit_parent_value or inherited_parent
    parent_id = str(parent_value) if parent_value not in (None, 0, "0", "") else ""
    if parent_id == comment_id:
        parent_id = ""
    node: dict[str, Any] = {
        "id": comment_id,
        "user": str(user.get("screen_name") or user.get("name") or ""),
        "text": text,
        "likes": _number(raw.get("like_count") or raw.get("attitudes_count")),
        "url": post_url + "#comment-%s" % comment_id,
        "discussion_type": "reply" if parent_id else "comment",
    }
    if parent_id:
        node["parent_id"] = parent_id
        if explicit_parent_value not in (None, 0, "0", "") and str(explicit_parent_value) != comment_id:
            node["_parent_id_explicit"] = True
    if raw.get("created_at"):
        node["created_at"] = str(raw["created_at"])
    used = 0 if already_seen else 1
    descendant_count = 0
    replies: list[dict] = []
    children = raw.get("comments")
    if isinstance(children, list):
        for child in children:
            child_id = str(child.get("idstr") or child.get("id") or "").strip() if isinstance(child, dict) else ""
            child_is_seen = seen_ids is not None and child_id in seen_ids
            if used >= remaining and not child_is_seen:
                coverage["truncated_by"].append("comment_limit")
                break
            child_node, child_used, _ = _comment_tree(
                child, post_url=post_url, remaining=remaining - used,
                inherited_parent=comment_id, depth=depth + 1,
                seen_ids=seen_ids, coverage=coverage)
            if child_node:
                replies.append(child_node)
                used += child_used
                descendant_count += child_used
    if replies:
        node["replies"] = replies
    return node, used, descendant_count


def _tree_nodes(node: dict):
    yield node
    for child in node.get("replies", []):
        yield from _tree_nodes(child)


async def fetch_discussion(
    post_url: str,
    *,
    post_id: str | int | None = None,
    max_comments: int = MAX_COMMENTS,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict, list[dict], dict]:
    """Fetch one status and bounded comment pages, reusing an optional Weibo cookie.

    The Weibo web API may return Sina's visitor page instead of JSON for an
    anonymous client. In that case the failure is recorded and no browser is
    opened; an existing exported ``weibo`` cookie is reused when available.
    """
    parts = _post_parts(post_url, post_id)
    identifier, canonical_url = parts if parts else ("", "")
    max_comments = max(0, int(max_comments))
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage: dict[str, Any] = {
        "status": "failed" if not parts else "partial",
        "platform": "weibo",
        "post_id": identifier,
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
    if not parts:
        coverage["errors"].append("invalid_status_url")
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
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))
    detail: dict[str, Any] = {"post_id": identifier, "url": canonical_url, "text": ""}

    async def collect(active: httpx.AsyncClient) -> tuple[dict, list[dict], dict]:
        status_payload, status_error = await _get_json(
            active, STATUS_API, params={"id": identifier}, headers=headers, deadline=deadline)
        status_info: dict[str, Any] = {}
        if status_error == "time_budget":
            coverage["truncated_by"].append("time_budget")
        elif status_error:
            coverage["errors"].append("status:%s" % status_error)
        elif status_payload is not None:
            status_info = status_payload.get("data") if isinstance(status_payload.get("data"), dict) else status_payload
            detail["text"] = _plain_text(status_info.get("text_raw") or status_info.get("text"))[:MAX_BODY_CHARS]
            user = status_info.get("user") if isinstance(status_info.get("user"), dict) else {}
            detail["author"] = str(user.get("screen_name") or "")
            if len(_plain_text(status_info.get("text_raw") or status_info.get("text"))) > MAX_BODY_CHARS:
                coverage["truncated_by"].append("status_text_limit")
            coverage["status_reported_comments"] = _number(status_info.get("comments_count"))
            if not detail["text"]:
                coverage["errors"].append("status:body_missing")

        comments: list[dict] = []
        max_id: Any = 0
        max_id_type: Any = 0
        reported: int | None = None
        root_has_more = False
        root_page_stalled = False
        reply_reserve = min(MAX_REPLY_RESERVE, max_comments // 4)
        root_node_limit = max_comments - reply_reserve
        if max_comments > 0:
            tree_index: dict[str, dict] = {}
            deferred_root_rows: list[Any] = []

            def index_tree(node: dict) -> None:
                tree_index[node["id"]] = node
                for child in node.get("replies", []):
                    index_tree(child)

            def merge_reply(node: dict, fallback_parent: dict) -> int:
                existing = tree_index.get(node["id"])
                if existing:
                    if node.pop("_parent_id_explicit", False):
                        parent_id = str(node.get("parent_id") or "")
                        if parent_id and parent_id != existing["id"]:
                            existing["parent_id"] = parent_id
                    return sum(merge_reply(child, existing)
                               for child in node.get("replies", []))
                node.pop("_parent_id_explicit", None)
                children = node.pop("replies", [])
                parent_id = str(node.get("parent_id") or fallback_parent["id"])
                parent = tree_index.get(parent_id) or fallback_parent
                parent.setdefault("replies", []).append(node)
                tree_index[node["id"]] = node
                return 1 + sum(merge_reply(child, node) for child in children)

            def rebuild_reply_trees() -> None:
                for root in comments:
                    nodes = list(_tree_nodes(root))
                    for node in nodes:
                        node.pop("_parent_id_explicit", None)
                        node.pop("replies", None)
                    by_id = {node["id"]: node for node in nodes}
                    root_id = root["id"]
                    for node in nodes[1:]:
                        parent_id = str(node.get("parent_id") or root_id)
                        parent = by_id.get(parent_id)
                        if parent is None or parent is node:
                            parent = root
                        parent.setdefault("replies", []).append(node)

            def add_root_rows(rows: list[Any], node_limit: int) -> None:
                for index, raw in enumerate(rows):
                    if coverage["fetched_comments"] >= node_limit:
                        deferred_root_rows.extend(rows[index:])
                        break
                    raw_id = str(raw.get("idstr") or raw.get("id") or "") if isinstance(raw, dict) else ""
                    if raw_id and raw_id in tree_index:
                        continue
                    seen_ids = set(tree_index)
                    node, used, reply_count = _comment_tree(
                        raw, post_url=canonical_url,
                        remaining=node_limit - coverage["fetched_comments"],
                        seen_ids=seen_ids, coverage=coverage)
                    if node:
                        comments.append(node)
                        index_tree(node)
                        coverage["fetched_roots"] += 1
                        coverage["fetched_replies"] += reply_count
                        coverage["fetched_comments"] += used

            async def fetch_root_page(node_limit: int) -> bool:
                nonlocal max_id, max_id_type, root_has_more, root_page_stalled, reported
                remaining_nodes = node_limit - coverage["fetched_comments"]
                if remaining_nodes <= 0:
                    return False
                payload, error = await _get_json(
                    active, COMMENTS_API,
                    params={"flow": 0, "is_reload": 1, "id": identifier,
                            "is_show_bulletin": 2, "is_mix": 0,
                            "fetch_level": 0,
                            "count": min(PAGE_SIZE, remaining_nodes),
                            "max_id": max_id, "max_id_type": max_id_type},
                    headers=headers, deadline=deadline)
                if error == "time_budget":
                    coverage["truncated_by"].append("time_budget")
                    return False
                if error:
                    coverage["errors"].append("comments:%s" % error)
                    return False
                assert payload is not None
                coverage["pages_attempted"] += 1
                count = payload.get("total_number")
                if isinstance(count, int) and count >= 0:
                    reported = max(reported or 0, count)
                rows = payload.get("data")
                if not isinstance(rows, list):
                    coverage["errors"].append("comments:invalid_data")
                    return False
                max_id = payload.get("max_id", 0)
                max_id_type = payload.get("max_id_type", 0)
                root_has_more = bool(_number(max_id))
                if not rows:
                    if root_has_more:
                        coverage["truncated_by"].append("empty_page_with_more")
                        root_page_stalled = True
                    return False
                add_root_rows(rows, node_limit)
                return True

            for _ in range(MAX_COMMENT_PAGES):
                if coverage["fetched_comments"] >= root_node_limit:
                    break
                if coverage["pages_attempted"] and not root_has_more:
                    break
                if not await fetch_root_page(root_node_limit):
                    break

            roots = [node for node in comments if not node.get("parent_id")]
            roots_with_inline_replies = [node for node in roots if node.get("replies")]
            roots_without_inline_replies = [node for node in roots if not node.get("replies")]
            reply_roots = roots_with_inline_replies + roots_without_inline_replies
            reported_replies_by_root: dict[str, int] = {}
            for root in reply_roots[:MAX_REPLY_THREADS]:
                root_id = root["id"]
                if coverage["fetched_comments"] >= max_comments:
                    coverage["truncated_by"].append("comment_limit")
                    break
                coverage["reply_threads_attempted"] += 1
                child_max_id: Any = 0
                child_max_id_type: Any = 0
                child_has_more = False
                for reply_page in range(MAX_REPLY_PAGES):
                    remaining_nodes = max_comments - coverage["fetched_comments"]
                    if remaining_nodes <= 0:
                        coverage["truncated_by"].append("comment_limit")
                        break
                    payload, error = await _get_json(
                        active, COMMENTS_API,
                        params={"flow": 0, "is_reload": 1, "id": root_id,
                                "is_show_bulletin": 2,
                                "is_mix": 0 if reply_page == 0 else 1,
                                "fetch_level": 1,
                                "count": min(PAGE_SIZE, remaining_nodes),
                                "max_id": child_max_id,
                                "max_id_type": child_max_id_type},
                        headers=headers, deadline=deadline)
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
                    count = payload.get("total_number")
                    if isinstance(count, int) and count >= 0:
                        reported_replies_by_root[root_id] = max(
                            reported_replies_by_root.get(root_id, 0), count)
                    child_max_id = payload.get("max_id", 0)
                    child_max_id_type = payload.get("max_id_type", 0)
                    child_has_more = bool(_number(child_max_id))
                    if not child_rows:
                        if child_has_more:
                            coverage["truncated_by"].append("empty_reply_page_with_more")
                        break
                    for raw in child_rows:
                        seen_ids = set(tree_index)
                        node, _, _ = _comment_tree(
                            raw, post_url=canonical_url,
                            remaining=max_comments - coverage["fetched_comments"],
                            inherited_parent=root_id, seen_ids=seen_ids,
                            coverage=coverage)
                        if not node:
                            continue
                        parent_id = str(node.get("parent_id") or root_id)
                        node["parent_id"] = parent_id
                        node["discussion_type"] = "reply"
                        added = merge_reply(node, root)
                        if not added:
                            continue
                        coverage["fetched_replies"] += added
                        coverage["fetched_comments"] += added
                    if coverage["fetched_comments"] >= max_comments:
                        if child_has_more:
                            coverage["truncated_by"].append("comment_limit")
                        break
                    if not child_has_more:
                        break
                else:
                    if child_has_more:
                        coverage["truncated_by"].append("reply_page_limit")

            coverage["reported_replies"] = sum(reported_replies_by_root.values())
            if deferred_root_rows:
                rows_to_flush = deferred_root_rows
                deferred_root_rows = []
                add_root_rows(rows_to_flush, max_comments)
                if deferred_root_rows:
                    coverage["truncated_by"].append("comment_limit")
            rebuild_reply_trees()
            for root in reply_roots[:MAX_REPLY_THREADS]:
                reported_count = reported_replies_by_root.get(root["id"])
                fetched_count = sum(1 for node in _tree_nodes(root)
                                    if node is not root)
                if reported_count is not None and fetched_count < reported_count:
                    coverage["truncated_by"].append("reply_count_mismatch")
            if len(reply_roots) > MAX_REPLY_THREADS:
                coverage["truncated_by"].append("reply_thread_limit")

            while (root_has_more and not root_page_stalled
                   and coverage["pages_attempted"] < MAX_COMMENT_PAGES
                   and coverage["fetched_comments"] < max_comments):
                if not await fetch_root_page(max_comments):
                    break

            rebuild_reply_trees()

            if root_has_more and not root_page_stalled:
                if coverage["fetched_comments"] >= max_comments:
                    coverage["truncated_by"].append("comment_limit")
                elif coverage["pages_attempted"] >= MAX_COMMENT_PAGES:
                    coverage["truncated_by"].append("page_limit")
        coverage["reported_comments"] = reported
        if reported is not None and coverage["fetched_roots"] < reported and not root_has_more:
            coverage["truncated_by"].append("comment_count_mismatch")
        coverage["truncated_by"] = list(dict.fromkeys(coverage["truncated_by"]))
        coverage["status"] = "partial" if (coverage["errors"] or coverage["truncated_by"]) else "complete"
        return detail, comments, coverage

    if client is not None:
        return await collect(client)
    async with httpx.AsyncClient(headers=headers, cookies=_load_cookies("weibo"),
                                 timeout=timeout, follow_redirects=True) as active:
        return await collect(active)
