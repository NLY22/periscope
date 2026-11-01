"""Discover public pages and optionally use the configured search provider."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

from src.url_security import UnsafeURLError, validate_http_url
from periscope.collection.crawling.engine import CrawlOptions
from periscope.search.identity import canonical_url, community_source_key
from .crawl import CollectionIncomplete, stream_crawl


def _safe_url(value: str) -> str:
    try:
        return canonical_url(validate_http_url(value))
    except (UnsafeURLError, ValueError, TypeError):
        return ""


def _result_source(url: str) -> tuple[str, str, dict, str]:
    """Match known discussions to platform identities and preserve their authorship tier."""
    from .browser_sources import identify_browser_url
    from .catalog import identify_community_url

    found = identify_community_url(url) or identify_browser_url(url)
    if found:
        source, identity = found
        return source, "community", identity, "community"
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host == "v2ex.com":
        import re
        match = re.fullmatch(r"/t/(\d+)/?", parsed.path)
        if match:
            return "v2ex_search", "community", {"source": "v2ex", "type": "topic", "native_id": match.group(1)}, "community"
    if host == "news.ycombinator.com" and parsed.path == "/item":
        ids = parse_qs(parsed.query).get("id", [])
        if len(ids) == 1 and ids[0].isdigit():
            return "hn_ai", "community", {"source": "hackernews", "type": "story", "native_id": ids[0]}, "community"
    source = community_source_key(url)
    return source or "public_web", "community" if source else "media", {"source": "web_crawl", "type": "document", "native_id": url}, "community" if source else "source_text"


async def _run_public_search(query: str, budget: float) -> list[dict]:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "periscope.investigations.web_worker",
                                                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL, **options)
    try:
        async with asyncio.timeout(max(0.001, min(15, budget))):
            output, _ = await process.communicate((json.dumps({"query": query}, ensure_ascii=True) + "\n").encode("ascii"))
    except BaseException:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        raise
    try:
        result = json.loads(output.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise CollectionIncomplete("public_web:invalid_response") from exc
    if process.returncode or not isinstance(result, dict) or not isinstance(result.get("results"), list):
        raise CollectionIncomplete("public_web:search_unavailable")
    return result["results"][:20]


async def stream_public_search(query: str, *, budget: float, depth: int = 2, detail_limit: int = 4):
    deadline = time.monotonic() + budget
    try:
        results = await _run_public_search(query, budget)
    except TimeoutError as exc:
        raise CollectionIncomplete("public_web:search_timeout") from exc
    candidates, seen = [], set()
    for item in results:
        if not isinstance(item, dict):
            continue
        url = _safe_url(str(item.get("href") or item.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        source, kind, identity, role = _result_source(url)
        card = {"source": source, "kind": kind, "_origin": "platform", "url": url,
                "title": str(item.get("title") or url), "text": str(item.get("body") or ""),
                "evidence": {**identity, "content_level": "search_result"},
                "provenance": {"method": "public_web_search", "content_role": role}}
        candidates.append(url)
        yield [card]
    incomplete = []
    if depth:
        for url in candidates[:max(0, detail_limit)]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                incomplete.append("time_budget")
                break
            options = CrawlOptions((url,), max_pages=1, depth=0, budget=remaining, include_seed=True)
            try:
                async for batch in stream_crawl(query, options):
                    retained = [row for row in batch if not (
                        (row.get("provenance") or {}).get("method") == "user_supplied"
                        and not row.get("text") and not row.get("comments") and not row.get("answers"))]
                    if retained:
                        yield retained
            except CollectionIncomplete as exc:
                incomplete.append(str(exc))
    if len(candidates) > (min(len(candidates), max(0, detail_limit)) if depth else 0):
        incomplete.append("candidate_limit" if depth else "depth_limit")
    if incomplete:
        raise CollectionIncomplete("public_web:" + ",".join(incomplete))


async def stream_search_api(query: str, *, budget: float, depth: int = 2):
    from periscope.search import api
    from .events import SourceProgress

    if not api.available():
        yield SourceProgress("search_api", "skipped", "not_configured")
        return
    response = await api.search_async(query, count=20, need_content=True, ori_question=query, timeout=min(15, budget))
    if not response.ok:
        raise CollectionIncomplete("search_api:unavailable")
    seen = set()
    for result in response.results:
        url = _safe_url(result.url)
        if not url or url in seen:
            continue
        seen.add(url)
        row = result.to_row()
        for field in ("title", "author", "text", "body", "date"):
            value = row.get(field)
            if not isinstance(value, str):
                row[field] = str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else ""
        source, kind, identity, role = _result_source(url)
        row.update(url=url, _origin="api",
                   source=source if role == "community" else row.get("source", "dsapi"), kind=kind,
                   evidence={**identity, "content_level": "api_content" if row.get("body") else "search_result"},
                   provenance={"method": "search_api", "content_role": role})
        yield [row]
