"""Enrich selected platform results and return patches for raw run records."""

from __future__ import annotations

import asyncio
import re
import time


def _enrich_topk(merged: list[dict], k: int = 5, *,
                 budget_seconds: float | None = 30.0) -> tuple[list[dict], dict, dict]:
    """Fill in details, discussions, and transcripts for top-K eligible rows.

    Eligible rows are B站 videos, HN stories, GitHub issues / PRs, V2EX topics, and Stack Overflow questions. Plain top-K
    selection can contain no supported platform item, so the scan skips rows
    that cannot be enriched and spends its limit on the first eligible ones.

    Returns `(merged, stat, patches)` where `patches` is keyed by URL. The patch
    map exists because the merge **copies** each row into its cluster head: a
    dict updated on `merged` never reaches the original `plat_rows` list, and
    the run shard is written from those originals. Patching both is what makes
    the enrichment visible in the shard and not only on screen.
    """
    stat = {"done": 0, "elapsed": 0.0, "failed": [],
            "no_transcript": 0, "targets": 0,
            "discussion_targets": 0, "discussion_done": 0,
            "discussion_partial": 0}
    bili_targets = []
    hn_targets = []
    github_targets = []
    v2ex_targets = []
    stack_targets = []
    for row in merged:
        if (len(bili_targets) + len(hn_targets) + len(github_targets)
                + len(v2ex_targets) + len(stack_targets) >= k):
            break
        source = str(row.get("source") or "")
        ids = row.get("ids") or {}
        if source in {"hn_ai", "hn_ai_monthly", "hn_cn"}:
            story_id = ids.get("objectID")
            if story_id is not None and str(story_id).isdigit():
                hn_targets.append((row, str(story_id), _row_key(row)))
            continue
        if source == "github_issues":
            match = re.match(
                r"^https?://github\.com/([^/]+)/([^/]+)/(issues|pull)/(\d+)(?:[/?#].*)?$",
                str(row.get("url") or ""), re.IGNORECASE)
            if match:
                github_targets.append((row, match.group(1), match.group(2),
                                       match.group(4), match.group(3) == "pull",
                                       _row_key(row)))
            continue
        if source == "v2ex_search":
            match = re.match(r"^https?://(?:www\.)?v2ex\.com/t/(\d+)(?:[/?#].*)?$",
                             str(row.get("url") or ""), re.IGNORECASE)
            if match:
                v2ex_targets.append((row, match.group(1), _row_key(row)))
            continue
        if source == "stackexchange_search":
            match = re.match(
                r"^https?://(?:www\.)?stackoverflow\.com/questions/(\d+)(?:[/?#].*)?$",
                str(row.get("url") or ""), re.IGNORECASE)
            question_id = (ids.get("question_id") or (match.group(1) if match else ""))
            if str(question_id).isdigit():
                stack_targets.append((row, str(question_id), _row_key(row)))
            continue
        bvid = ids.get("bvid")
        if not bvid and (row.get("url") or ""):
            match = re.search(r"(BV[0-9A-Za-z]{10})", row["url"])
            bvid = match.group(1) if match else None
        if bvid:
            bili_targets.append((row, bvid, _row_key(row)))
    stat["targets"] = (len(bili_targets) + len(hn_targets) + len(github_targets)
                        + len(v2ex_targets) + len(stack_targets))
    stat["discussion_targets"] = len(hn_targets)
    stat["bilibili_discussion_targets"] = len(bili_targets)
    stat["bilibili_discussion_done"] = 0
    stat["bilibili_discussion_partial"] = 0
    stat["github_discussion_targets"] = len(github_targets)
    stat["github_discussion_done"] = 0
    stat["github_discussion_partial"] = 0
    stat["v2ex_discussion_targets"] = len(v2ex_targets)
    stat["v2ex_discussion_done"] = 0
    stat["v2ex_discussion_partial"] = 0
    stat["stack_discussion_targets"] = len(stack_targets)
    stat["stack_discussion_done"] = 0
    stat["stack_discussion_partial"] = 0
    if not stat["targets"]:
        return merged, stat, {}

    t0 = time.monotonic()
    def remaining_budget() -> float | None:
        return None if budget_seconds is None else max(0.0, budget_seconds - (time.monotonic() - t0))

    patches: dict = {}
    if bili_targets:
        try:
            from periscope.collection.platforms import bilibili as bilibili_enrich
            got = asyncio.run(bilibili_enrich.enrich_many(
                [bvid for _row, bvid, _key in bili_targets],
                concurrency=4, with_comments=True,
                budget_seconds=remaining_budget()))
        except Exception as exc:  # noqa: BLE001
            stat["failed"].append("bilibili:%s" % type(exc).__name__)
            got = {}

        for row, bvid, key in bili_targets:
            result = got.get(bvid)
            if result is None:
                stat["failed"].append(bvid)
                continue
            patch = result.as_patch()
            row.update(patch)
            if key:
                patches[key] = patch
            if result.metrics or result.has_transcript or result.comments:
                stat["done"] += 1
            if result.discussion_coverage:
                stat["bilibili_discussion_done"] += 1
                if result.discussion_coverage.get("status") != "complete":
                    stat["bilibili_discussion_partial"] += 1
            if not result.has_transcript:
                stat["no_transcript"] += 1
            if result.errors:
                stat["failed"].extend("%s:%s" % (bvid, e) for e in result.errors[:1])
            if result.discussion_coverage.get("errors"):
                stat["failed"].extend("%s:%s" % (bvid, e)
                                      for e in result.discussion_coverage["errors"][:1])

    if hn_targets:
        try:
            from periscope.collection.platforms import hackernews
            got = asyncio.run(hackernews.enrich_many(
                [story_id for _row, story_id, _key in hn_targets],
                concurrency=4, budget_seconds=remaining_budget()))
        except Exception as exc:  # noqa: BLE001
            stat["failed"].append("hackernews:%s" % type(exc).__name__)
            got = {}

        for row, story_id, key in hn_targets:
            result = got.get(story_id)
            if result is None:
                stat["failed"].append("hn:%s" % story_id)
                continue
            comments, coverage = result
            patch = {"discussion_coverage": coverage}
            if comments:
                patch["comments"] = comments
            row.update(patch)
            if key:
                patches[key] = patch
            stat["discussion_done"] += 1
            if coverage.get("status") != "complete":
                stat["discussion_partial"] += 1
            stat["done"] += 1
            stat["failed"].extend("hn:%s:%s" % (story_id, error)
                                  for error in coverage.get("errors", [])[:1])

    if github_targets:
        try:
            from periscope.collection.platforms import github
            got = {}
            for _row, owner, repo, number, is_pull_request, key in github_targets:
                remaining = remaining_budget()
                if remaining == 0:
                    got[key] = ([], {"status": "partial", "fetched_comments": 0,
                                     "truncated_by": ["time_budget"], "errors": []})
                    continue
                try:
                    got[key] = asyncio.run(github.fetch_discussion(
                        owner, repo, number, is_pull_request=is_pull_request,
                        budget_seconds=remaining))
                except Exception as exc:  # noqa: BLE001 - keep other rows usable
                    stat["failed"].append("github:%s/%s#%s:%s" % (
                        owner, repo, number, type(exc).__name__))
        except Exception as exc:  # noqa: BLE001 - report per-source enrichment failure
            stat["failed"].append("github:%s" % type(exc).__name__)
            got = {}

        for row, owner, repo, number, is_pull_request, key in github_targets:
            result = got.get(key)
            if result is None:
                stat["failed"].append("github:%s/%s#%s" % (owner, repo, number))
                continue
            comments, coverage = result
            patch = {"discussion_coverage": coverage}
            if comments:
                patch["comments"] = comments
            row.update(patch)
            if key:
                patches[key] = patch
            stat["github_discussion_done"] += 1
            stat["done"] += 1
            if coverage.get("status") != "complete":
                stat["github_discussion_partial"] += 1
            stat["failed"].extend(
                "github:%s/%s#%s:%s" % (owner, repo, number, error)
                for error in coverage.get("errors", [])[:1])

    if v2ex_targets:
        try:
            from periscope.collection.platforms import v2ex
        except Exception as exc:  # noqa: BLE001
            stat["failed"].append("v2ex:%s" % type(exc).__name__)
            got = {}
        else:
            got = {}
            for _row, topic_id, key in v2ex_targets:
                remaining = remaining_budget()
                if remaining == 0:
                    got[key] = ({}, [], {"status": "partial", "fetched_replies": 0,
                                         "truncated_by": ["time_budget"], "errors": []})
                    continue
                try:
                    got[key] = asyncio.run(v2ex.fetch_discussion(
                        topic_id, budget_seconds=remaining))
                except Exception as exc:  # noqa: BLE001 - keep other rows usable
                    stat["failed"].append("v2ex:%s:%s" % (topic_id, type(exc).__name__))

        for row, topic_id, key in v2ex_targets:
            result = got.get(key)
            if result is None:
                stat["failed"].append("v2ex:%s" % topic_id)
                continue
            detail, comments, coverage = result
            patch = {"discussion_coverage": coverage}
            if detail.get("text"):
                patch["text"] = detail["text"]
            if detail.get("author") and not row.get("author"):
                patch["author"] = detail["author"]
            topic_detail = {field: detail[field] for field in ("topic_id", "node", "created_at")
                            if detail.get(field) not in (None, "")}
            if topic_detail:
                patch["topic_detail"] = topic_detail
            if comments:
                patch["comments"] = comments
            row.update(patch)
            if key:
                patches[key] = patch
            stat["v2ex_discussion_done"] += 1
            stat["done"] += 1
            if coverage.get("status") != "complete":
                stat["v2ex_discussion_partial"] += 1
            stat["failed"].extend("v2ex:%s:%s" % (topic_id, error)
                                  for error in coverage.get("errors", [])[:1])

    if stack_targets:
        try:
            from periscope.collection.platforms import stackoverflow
        except Exception as exc:  # noqa: BLE001
            stat["failed"].append("stackoverflow:%s" % type(exc).__name__)
            got = {}
        else:
            got = {}
            for _row, question_id, key in stack_targets:
                remaining = remaining_budget()
                if remaining == 0:
                    got[key] = ({}, [], [], {"status": "partial", "fetched_answers": 0,
                                              "fetched_comments": 0, "truncated_by": ["time_budget"],
                                              "errors": []})
                    continue
                try:
                    got[key] = asyncio.run(stackoverflow.fetch_discussion(
                        question_id, budget_seconds=remaining))
                except Exception as exc:  # noqa: BLE001 - keep other rows usable
                    stat["failed"].append("stackoverflow:%s:%s" % (
                        question_id, type(exc).__name__))

        for row, question_id, key in stack_targets:
            result = got.get(key)
            if result is None:
                stat["failed"].append("stackoverflow:%s" % question_id)
                continue
            question, answers, comments, coverage = result
            patch = {"discussion_coverage": coverage}
            if question.get("text"):
                patch["text"] = question["text"]
            if question.get("author") and not row.get("author"):
                patch["author"] = question["author"]
            detail = question.get("detail") or {}
            if detail:
                patch["question_detail"] = detail
            if answers:
                patch["answers"] = answers
            if comments:
                patch["comments"] = comments
            row.update(patch)
            if key:
                patches[key] = patch
            stat["stack_discussion_done"] += 1
            stat["done"] += 1
            if coverage.get("status") != "complete":
                stat["stack_discussion_partial"] += 1
            stat["failed"].extend("stackoverflow:%s:%s" % (question_id, error)
                                  for error in coverage.get("errors", [])[:1])

    stat["elapsed"] = time.monotonic() - t0
    return merged, stat, patches


def _row_key(row: dict) -> str:
    """Stable identity for matching a merged row back to its original."""
    return (row.get("url") or "").strip() or (row.get("title") or "").strip()
