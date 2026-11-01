"""Stream existing public source searches and selected discussion details."""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from periscope.collection.registry import SourcePlan, SourceRegistry
from periscope.collection.strategies import broad
from periscope.search.identity import canonical_url
from .crawl import CollectionIncomplete
from .reuse import _terms


CATALOG_SOURCES = ("zhihu_search", "weibo_search", "bili_search", "csdn_search", "juejin_search",
                   "github_issues", "stackexchange_search",
                   "arxiv_ai", "openalex", "crossref_works")
ACADEMIC = {"arxiv_ai": "arxiv", "openalex": "openalex", "crossref_works": "crossref"}
_COMMUNITY_HOSTS = {
    "zhihu_search": ("zhihu", {"zhihu.com", "www.zhihu.com", "zhuanlan.zhihu.com"}),
    "weibo_search": ("weibo", {"weibo.com", "www.weibo.com"}),
    "bili_search": ("bilibili", {"bilibili.com", "www.bilibili.com"}),
    "csdn_search": ("csdn", {"csdn.net", "www.csdn.net", "blog.csdn.net"}),
    "juejin_search": ("juejin", {"juejin.cn", "www.juejin.cn"}),
    "github_issues": ("github", {"github.com"}),
    "stackexchange_search": ("stackoverflow", {"stackoverflow.com"}),
}


def _unsupported_lead(source_key: str, url: str):
    """Keep a search lead on the expected host without promising deep capture."""
    source, hosts = _COMMUNITY_HOSTS.get(source_key, ("", set()))
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in hosts:
        return None
    identity = canonical_url(url)
    return {"source": source, "type": "document", "native_id": identity} if identity else None


def _search_error(report: dict) -> str:
    """Expose actionable codes without retaining arbitrary remote error text."""
    error = str(report.get("error") or "")
    match = re.search(r"\bHTTP\s+(\d{3})\b", error, re.IGNORECASE)
    if match:
        return "search_http_" + match.group(1)
    if report.get("status") in {"timeout", "over_budget"}:
        return "search_time_budget"
    return "search_unavailable"


def _target(source_key: str, url: str, *, ids=None, evidence=None):
    """Return a stable source identity only for the adapter's actual host."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    if source_key == "github_issues" and host == "github.com":
        match = re.fullmatch(r"/([\w.-]+)/([\w.-]+)/(issues|pull)/(\d+)/?", parsed.path)
        if match:
            owner, repo, kind, number = match.groups()
            return {"source": "github", "type": "pull_request" if kind == "pull" else "issue",
                    "native_id": owner + "/" + repo + "#" + number}, (owner, repo, number, kind == "pull")
    if source_key == "stackexchange_search" and host == "stackoverflow.com":
        match = re.match(r"^/questions/(\d+)(?:/|$)", parsed.path)
        if match:
            identifier = match.group(1)
            return {"source": "stackoverflow", "type": "question", "native_id": identifier}, (identifier,)
    if source_key == "csdn_search" and host == "blog.csdn.net":
        from periscope.collection.platforms.csdn import _article_parts
        parts = _article_parts(url)
        if parts:
            return {"source": "csdn", "type": "article", "native_id": parts[0]}, (parts[1],)
    if source_key == "juejin_search" and host in {"juejin.cn", "www.juejin.cn"}:
        from periscope.collection.platforms.juejin import _article_id
        parts = _article_id(url)
        if parts:
            return {"source": "juejin", "type": "article", "native_id": parts[0]}, (parts[1],)
    if source_key == "zhihu_search" and host in {"zhihu.com", "www.zhihu.com"}:
        from periscope.collection.platforms.zhihu import _answer_id
        parts = _answer_id(url)
        if parts:
            return {"source": "zhihu", "type": "answer", "native_id": parts[0]}, (parts[1],)
    if source_key == "weibo_search" and host in {"weibo.com", "www.weibo.com"}:
        from periscope.collection.platforms.weibo import _post_parts
        post_id = (ids or {}).get("id") or (ids or {}).get("mid") or (evidence or {}).get("native_id")
        parts = _post_parts(url, post_id)
        display = _post_parts(url, None)
        if parts and display:
            identity = {"source": "weibo", "type": "post", "native_id": display[0]}
            if parts[0] != display[0]:
                identity["aliases"] = [parts[0]]
            return identity, (parts[1], parts[0])
    if source_key == "bili_search" and host in {"bilibili.com", "www.bilibili.com"}:
        match = re.match(r"^/video/(BV[0-9A-Za-z]{10}|av[0-9]+)(?:/|$)", parsed.path)
        if match:
            original_id = match.group(1)
            identifier = original_id
            bvid = str((ids or {}).get("bvid") or (evidence or {}).get("native_id") or "")
            if identifier.startswith("av") and re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
                identifier = bvid
            identity = {"source": "bilibili", "type": "video", "native_id": identifier}
            if original_id != identifier:
                identity["aliases"] = [original_id]
            return identity, (identifier,)
    if source_key in ACADEMIC:
        identity = canonical_url(url)
        if identity:
            return {"source": ACADEMIC[source_key], "type": "paper", "native_id": identity}, ()
    return None


def identify_community_url(url: str):
    """Recognize a directly supplied discussion only when an adapter supports it."""
    for source_key in CATALOG_SOURCES:
        if source_key in ACADEMIC:
            continue
        target = _target(source_key, url)
        if target:
            identity, _ = target
            return source_key, identity
    return None


async def stream_catalog_detail(card: dict, *, budget: float, max_comments: int = 60):
    """Read one already-discovered item; save an available GitHub body first."""
    source_key = card.get("source") or ""
    target = _target(source_key, card.get("url") or "", ids=card.get("ids"), evidence=card.get("evidence"))
    if target is None or source_key in ACADEMIC:
        raise CollectionIncomplete(source_key + ":invalid_target")
    _, args = target
    deadline = time.monotonic() + budget
    incomplete = []
    if source_key == "github_issues":
        from periscope.collection.platforms import github
        owner, repo, number, is_pull = args
        detail, detail_coverage = await github.fetch_issue_detail(owner, repo, number, budget_seconds=budget)
        current = card
        if detail:
            current = {**card, "text": detail.get("text") or "", "author": detail.get("author") or card.get("author") or "",
                       "evidence": {**card["evidence"], "content_level": "full"},
                       "discussion_coverage": {"status": "not_attempted"},
                       "provenance": {**card.get("provenance", {}), "method": "github_api"}}
            yield [current]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CollectionIncomplete(source_key + ":time_budget")
        comments, coverage = await github.fetch_discussion(owner, repo, number, is_pull_request=is_pull,
                                                            max_comments=max_comments, budget_seconds=remaining)
        if detail_coverage.get("status") != "complete":
            incomplete.append("detail_incomplete")
        yield [{**current, "comments": comments, "discussion_coverage": coverage}]
    elif source_key == "stackexchange_search":
        from periscope.collection.platforms import stackoverflow
        answer_limit = min(5, max_comments)
        detail, answers, comments, coverage = await stackoverflow.fetch_discussion(
            args[0], max_answers=answer_limit, max_comments=max(0, max_comments - answer_limit), budget_seconds=budget)
        current = {**card, "text": detail.get("text") or card.get("text") or "",
                   "author": detail.get("author") or card.get("author") or "",
                   "evidence": {**card["evidence"], "content_level": "full" if detail.get("text") else card["evidence"].get("content_level", "search_result")},
                   "answers": answers, "comments": comments, "discussion_coverage": coverage,
                   "provenance": {**card.get("provenance", {}), "method": "stackoverflow_api"}}
        yield [current]
    elif source_key in {"zhihu_search", "weibo_search", "bili_search"}:
        if source_key == "zhihu_search":
            from periscope.collection.platforms import zhihu as platform
            detail, comments, coverage = await platform.fetch_discussion(args[0], max_comments=max_comments, budget_seconds=budget)
        elif source_key == "weibo_search":
            from periscope.collection.platforms import weibo as platform
            detail, comments, coverage = await platform.fetch_discussion(args[0], post_id=args[1], max_comments=max_comments, budget_seconds=budget)
        else:
            from periscope.collection.platforms import bilibili as platform
            detail, comments, coverage = await platform.fetch_video_discussion(args[0], max_comments=max_comments, budget_seconds=budget)
        evidence = dict(card["evidence"])
        if source_key == "bili_search":
            aliases = list(evidence.get("aliases") or [])
            if str(detail.get("aid") or "").isdigit():
                aliases.append("av" + str(detail["aid"]))
            if re.fullmatch(r"BV[0-9A-Za-z]{10}", str(detail.get("bvid") or "")):
                aliases.append(detail["bvid"])
            evidence["aliases"] = list(dict.fromkeys(aliases))
        current = {**card, "text": detail.get("text") or card.get("text") or "",
                   "author": detail.get("author") or card.get("author") or "",
                   "evidence": {**evidence, "content_level": "full" if detail.get("text") else card["evidence"].get("content_level", "search_result")},
                   "comments": comments, "discussion_coverage": coverage,
                   "provenance": {**card.get("provenance", {}), "method": source_key + "_detail"}}
        yield [current]
    else:
        if source_key == "csdn_search":
            from periscope.collection.platforms import csdn as platform
        else:
            from periscope.collection.platforms import juejin as platform
        detail, comments, coverage = await platform.fetch_discussion(args[0], max_comments=max_comments, budget_seconds=budget)
        current = {**card, "text": detail.get("text") or card.get("text") or "",
                   "author": detail.get("author") or card.get("author") or "",
                   "evidence": {**card["evidence"], "content_level": "full" if detail.get("text") else card["evidence"].get("content_level", "search_result")},
                   "comments": comments, "discussion_coverage": coverage,
                   "provenance": {**card.get("provenance", {}), "method": source_key + "_detail"}}
        yield [current]
    if coverage.get("status") != "complete":
        reasons = [str(item) for item in (coverage.get("errors") or []) + (coverage.get("truncated_by") or [])]
        known = ("login_required", "time_budget", "comment_limit", "page_limit", "reply_page_limit")
        found = []
        for reason in reasons:
            match = re.search(r"(?:^|:)(login_required|time_budget|comment_limit|page_limit|reply_page_limit|http_\d{3})(?:$|:)", reason)
            if match and (match.group(1) in known or match.group(1).startswith("http_")):
                found.append("discussion_" + match.group(1))
        incomplete.extend(found or ["discussion_incomplete"])
    if incomplete:
        raise CollectionIncomplete(source_key + ":" + ",".join(incomplete))


async def stream_catalog_source(topic: str, source_key: str, *, budget: float,
                                detail_limit: int = 4, max_comments: int = 60, depth: int = 2):
    """Save search candidates promptly, then retrieve a bounded set of details."""
    if source_key not in CATALOG_SOURCES:
        raise ValueError("unsupported investigation source")
    deadline = time.monotonic() + budget
    entry = SourceRegistry().get(source_key)
    rows, stats = await broad.collect_async(topic, SourcePlan((entry,)), pages=1,
                                            budget=min(30, budget), timeout=min(15, budget), concurrency=1)
    reports = stats.get("source_reports") or []
    incomplete = []
    for report in reports:
        if report.get("status") in {"failed", "partial", "timeout", "over_budget"}:
            incomplete.append(_search_error(report))
    candidates, seen = [], set()
    for row in rows:
        target = _target(source_key, row.get("url") or "", ids=row.get("ids"))
        if target is None:
            incomplete.append("unsupported_target")
            identity = _unsupported_lead(source_key, row.get("url") or "")
            if identity:
                key = (identity["source"], identity["type"], identity["native_id"])
                if key not in seen:
                    seen.add(key)
                    yield [{**row, "source": source_key, "kind": entry.kind, "_origin": "platform",
                            "evidence": {**identity, "content_level": "search_result"},
                            "discussion_coverage": {"status": "unsupported"},
                            "provenance": {**(row.get("provenance") or {}), "method": "platform_search", "content_role": "community"}}]
            continue
        identity, args = target
        key = (identity["source"], identity["type"], identity["native_id"])
        if key in seen:
            continue
        seen.add(key)
        role = "community" if source_key not in ACADEMIC else "source_text"
        card = {**row, "source": source_key, "kind": entry.kind, "_origin": "platform",
                "evidence": {**identity, "content_level": "search_result"},
                "discussion_coverage": {"status": "not_attempted"},
                "provenance": {**(row.get("provenance") or {}), "method": "platform_search", "content_role": role}}
        candidates.append((card, args))
        yield [card]
    if source_key in ACADEMIC:
        if incomplete:
            raise CollectionIncomplete(source_key + ":" + ",".join(incomplete))
        return

    followed = 0
    if depth > 0:
        query_terms = _terms(topic)
        candidates.sort(key=lambda pair: -len(query_terms & _terms(
            str(pair[0].get("title") or "") + " " + str(pair[0].get("text") or ""))))
        for card, args in candidates[:max(0, detail_limit)]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                incomplete.append("time_budget")
                break
            followed += 1
            try:
                async for batch in stream_catalog_detail(card, budget=remaining, max_comments=max_comments):
                    yield batch
            except CollectionIncomplete as exc:
                incomplete.append(str(exc))
    if candidates and len(candidates) > followed:
        incomplete.append("depth_limit" if depth == 0 else "candidate_limit")
    if incomplete:
        raise CollectionIncomplete(source_key + ":" + ",".join(dict.fromkeys(incomplete)))
