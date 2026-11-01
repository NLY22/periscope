"""Zero-key public web search provider using an isolated worker process."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from urllib.parse import urlsplit

from periscope.search.api import Result, SearchResponse
from periscope.search.identity import canonical_url


def _extract_site_name(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return "web"


async def _run_worker(query: str, timeout: float = 6.0) -> list[Result]:
    """通过独立子进程运行搜索，超时能被安全强制 kill。"""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "periscope.investigations.web_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **options,
        )
    except Exception:
        return []

    try:
        payload = (json.dumps({"query": query}, ensure_ascii=True) + "\n").encode("ascii")
        output, _ = await asyncio.wait_for(process.communicate(payload), timeout=timeout)
    except (asyncio.TimeoutError, BaseException):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await process.wait()
            except Exception:
                pass
        return []

    try:
        data = json.loads(output.decode("utf-8"))
    except Exception:
        return []

    results: list[Result] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or item.get("url") or "").strip()
        if not href or not href.startswith("http"):
            continue
        clean_url = canonical_url(href)
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        site = _extract_site_name(clean_url)
        results.append(
            Result(
                title=title,
                url=clean_url,
                site_name=site,
                snippet=body,
                summary=body[:300] if body else "",
                content=body,
                authority=1,
                rerank_score=1.0,
            )
        )
    return results


async def search_public_web_async(
    query: str,
    variants: list[str] | None = None,
    count: int = 25,
    timeout: float = 10.0,
) -> SearchResponse:
    """异步多变体公网检索。"""
    t0 = time.monotonic()
    queries = [query]
    if variants:
        for v in variants:
            if v and v.lower() not in [q.lower() for q in queries]:
                queries.append(v)

    # 优先查主词，若配置了变体并行查询 1 个变体
    target_queries = queries[:2]
    tasks = [_run_worker(q, timeout=min(6.0, timeout)) for q in target_queries]

    try:
        done = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:  # noqa: BLE001
        return SearchResponse(elapsed=time.monotonic() - t0, error=str(exc))

    merged: dict[str, Result] = {}
    for batch in done:
        if isinstance(batch, list):
            for r in batch:
                if r.url and r.url not in merged:
                    merged[r.url] = r

    elapsed = time.monotonic() - t0
    results = list(merged.values())
    if not results:
        return SearchResponse(elapsed=elapsed, error="timeout_or_empty")
    return SearchResponse(results=results[:count], elapsed=elapsed)


def search_public_web(
    query: str,
    variants: list[str] | None = None,
    count: int = 25,
    timeout: float = 10.0,
) -> SearchResponse:
    """同步包装入口。"""
    try:
        return asyncio.run(search_public_web_async(query, variants=variants, count=count, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        return SearchResponse(error=str(exc))
