"""Bounded Stack Overflow question, answer, and comment retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import asyncio
import time

import httpx

from periscope.collection.transport.parsing import _clean


API_ROOT = "https://api.stackexchange.com/2.3"
MAX_ANSWERS = 10
MAX_COMMENTS = 60
PAGE_SIZE = 100
REQUEST_TIMEOUT = 8.0


def _endpoint(path: str) -> str:
    return API_ROOT + path


async def _request(client: httpx.AsyncClient, path: str, params: dict,
                   deadline: float | None) -> tuple[list[dict], bool, str | None]:
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return [], False, "time_budget"
    try:
        request = client.get(_endpoint(path), params=params)
        response = await request if remaining is None else await asyncio.wait_for(request, remaining)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return [], False, "invalid_payload"
        if payload.get("error_id"):
            return [], False, "api_error_%s" % payload["error_id"]
        items = payload.get("items")
        if not isinstance(items, list):
            return [], False, "missing_items"
        return [item for item in items if isinstance(item, dict)], bool(payload.get("has_more")), None
    except asyncio.TimeoutError:
        return [], False, "time_budget"
    except httpx.HTTPStatusError as exc:
        return [], False, "http_%d" % exc.response.status_code
    except Exception as exc:  # noqa: BLE001 - keep errors local to the source
        return [], False, type(exc).__name__


def _user(item: dict) -> str:
    owner = item.get("owner") or {}
    return str(owner.get("display_name") or "")


def _created(item: dict) -> int | None:
    value = item.get("creation_date")
    return int(value) if isinstance(value, (int, float)) else None


def _question(items: list[dict], question_id: str) -> dict | None:
    item = next((entry for entry in items if str(entry.get("question_id")) == question_id), None)
    if item is None:
        return None
    body = _clean(item.get("body"))
    detail = {
        "text": body,
        "author": _user(item),
        "question_id": question_id,
        "detail": {
            key: item[key] for key in ("accepted_answer_id", "answer_count", "comment_count",
                                      "score", "view_count", "tags", "last_activity_date")
            if item.get(key) is not None
        },
    }
    if _created(item) is not None:
        detail["created_at"] = _created(item)
    return detail


def _answers(items: list[dict], question_id: str, limit: int,
             accepted_answer_id: Any = None) -> tuple[list[dict], int]:
    ordered = list(items)
    if accepted_answer_id is not None:
        accepted = next((item for item in ordered
                         if str(item.get("answer_id")) == str(accepted_answer_id)), None)
        if accepted is not None:
            ordered = [accepted] + [item for item in ordered if item is not accepted]
    out = []
    for item in ordered:
        answer_id = item.get("answer_id")
        body = _clean(item.get("body"))
        if answer_id is None or not body:
            continue
        node = {
            "id": str(answer_id),
            "user": _user(item),
            "text": body,
            "parent_id": question_id,
            "url": str(item.get("link") or "https://stackoverflow.com/a/%s" % answer_id),
            "discussion_type": "answer",
            "replies": [],
        }
        if isinstance(item.get("score"), (int, float)):
            node["score"] = item["score"]
        if _created(item) is not None:
            node["created_at"] = _created(item)
        out.append(node)
    return out[:limit], len(out)


def _comments(items: list[dict], question_id: str,
              answer_ids: set[str]) -> tuple[list[dict], dict[str, list[dict]]]:
    question_comments = []
    answer_comments: dict[str, list[dict]] = {answer_id: [] for answer_id in answer_ids}
    for item in items:
        comment_id = item.get("comment_id")
        body = _clean(item.get("body"))
        if comment_id is None or not body:
            continue
        post_id = str(item.get("post_id") or "")
        node = {
            "id": str(comment_id),
            "user": _user(item),
            "text": body,
            "parent_id": post_id,
            "url": str(item.get("link") or ""),
            "discussion_type": str(item.get("post_type") or "comment"),
        }
        reply_to = item.get("reply_to_user")
        if isinstance(reply_to, dict) and reply_to.get("display_name"):
            node["reply_to_user"] = str(reply_to["display_name"])
        if isinstance(item.get("score"), (int, float)):
            node["score"] = item["score"]
        if _created(item) is not None:
            node["created_at"] = _created(item)
        if post_id == question_id:
            question_comments.append(node)
        elif post_id in answer_comments:
            answer_comments[post_id].append(node)
    return question_comments, answer_comments


async def fetch_discussion(
    question_id: int | str,
    *,
    max_answers: int = MAX_ANSWERS,
    max_comments: int = MAX_COMMENTS,
    budget_seconds: float | None = 20.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict, list[dict], list[dict], dict]:
    """Fetch one question, its highest-voted answers, and post-level comments.

    Stack Exchange comments can be directed at a user, but the API does not
    provide a parent comment ID. They are grouped under their question or answer,
    and `reply_to_user` is retained without inventing a comment reply chain.
    """
    question_id = str(question_id).strip()
    valid = question_id.isdigit()
    max_answers = max(0, int(max_answers))
    max_comments = max(0, int(max_comments))
    started = time.monotonic()
    deadline = started + max(0.0, budget_seconds) if budget_seconds is not None else None
    coverage = {
        "status": "failed" if not valid else "partial",
        "question_id": question_id if valid else "",
        "reported_answers": None,
        "returned_answers": 0,
        "fetched_answers": 0,
        "returned_comments": 0,
        "fetched_comments": 0,
        "max_answers": max_answers,
        "max_comments": max_comments,
        "truncated_by": [],
        "errors": [],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if not valid:
        coverage["errors"].append("invalid_question_id")
        return {}, [], [], coverage

    params = {"site": "stackoverflow", "filter": "withbody"}
    timeout = REQUEST_TIMEOUT if budget_seconds is None else max(0.1, min(REQUEST_TIMEOUT, budget_seconds))

    async def collect(active_client: httpx.AsyncClient):
        requests = {
            "question": asyncio.create_task(_request(
                active_client, "/questions/%s" % question_id, params, deadline)),
            "answers": asyncio.create_task(_request(
                active_client, "/questions/%s/answers" % question_id,
                {**params, "sort": "votes", "order": "desc", "pagesize": PAGE_SIZE, "page": 1}, deadline)),
        }
        wait = None if deadline is None else max(0.0, deadline - time.monotonic())
        done, pending = await asyncio.wait(requests.values(), timeout=wait)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            coverage["truncated_by"].append("time_budget")

        result: dict[str, list[dict]] = {}
        more: dict[str, bool] = {}
        for key, task in requests.items():
            if task not in done:
                continue
            items, has_more, error = task.result()
            result[key] = items
            more[key] = has_more
            if error == "time_budget":
                if "time_budget" not in coverage["truncated_by"]:
                    coverage["truncated_by"].append(error)
            elif error:
                coverage["errors"].append("%s:%s" % (key, error))
            if has_more:
                coverage["truncated_by"].append("%s_page_limit" % key)

        question = _question(result.get("question", []), question_id) or {}
        if not question and "question" in result:
            coverage["errors"].append("question:missing")
        answer_count = question.get("detail", {}).get("answer_count")
        if isinstance(answer_count, int) and answer_count >= 0:
            coverage["reported_answers"] = answer_count
        raw_answers = result.get("answers", [])
        answers, normalized_answer_count = _answers(
            raw_answers, question_id, max_answers,
            question.get("detail", {}).get("accepted_answer_id"))
        coverage["returned_answers"] = len(raw_answers)
        if normalized_answer_count > max_answers or (
                isinstance(answer_count, int) and answer_count > len(answers)):
            coverage["truncated_by"].append("answer_limit")
        coverage["fetched_answers"] = len(answers)

        question_comments: list[dict] = []
        answer_comments: dict[str, list[dict]] = {}
        post_ids = [question_id] + [answer["id"] for answer in answers]
        remaining = None if deadline is None else deadline - time.monotonic()
        raw_comments: list[dict] = []
        if max_comments and remaining != 0:
            comment_items, comments_more, error = await _request(
                active_client, "/posts/%s/comments" % ";".join(post_ids),
                {**params, "pagesize": PAGE_SIZE, "page": 1}, deadline)
            raw_comments = comment_items
            if error == "time_budget":
                if "time_budget" not in coverage["truncated_by"]:
                    coverage["truncated_by"].append(error)
            elif error:
                coverage["errors"].append("comments:%s" % error)
            if comments_more:
                coverage["truncated_by"].append("comments_page_limit")
        elif max_comments == 0:
            coverage["truncated_by"].append("comment_limit")
        else:
            coverage["truncated_by"].append("time_budget")

        question_comments, answer_comments = _comments(raw_comments, question_id,
                                                       {answer["id"] for answer in answers})
        coverage["returned_comments"] = len(raw_comments)
        combined = question_comments + [comment for values in answer_comments.values() for comment in values]
        if len(combined) > max_comments:
            coverage["truncated_by"].append("comment_limit")
            keep = {comment["id"] for comment in combined[:max_comments]}
            question_comments = [comment for comment in question_comments if comment["id"] in keep]
            for answer_id in answer_comments:
                answer_comments[answer_id] = [comment for comment in answer_comments[answer_id]
                                              if comment["id"] in keep]
        coverage["fetched_comments"] = len(question_comments) + sum(
            len(values) for values in answer_comments.values())
        for answer in answers:
            answer["replies"] = answer_comments.get(answer["id"], [])
            if not answer["replies"]:
                answer.pop("replies")

        if not coverage["truncated_by"] and not coverage["errors"]:
            coverage["status"] = "complete"
        elif question or answers or question_comments:
            coverage["status"] = "partial"
        elif coverage["errors"] or coverage["truncated_by"]:
            coverage["status"] = "partial"
        else:
            coverage["status"] = "unknown"
        return question, answers, question_comments, coverage

    if client is not None:
        return await collect(client)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": "Periscope-Research/1.0"}) as active_client:
        return await collect(active_client)
