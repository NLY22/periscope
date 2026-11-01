"""Fast asynchronous fulltext hydration and metadata enrichment for search results."""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit
import httpx

from periscope.search.api import Result

_DATE_PATTERNS = [
    re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})"),
    re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"),
]


def _extract_meta(html: str) -> dict[str, str]:
    """从 HTML 中快速提取 meta 级发布时间与作者。"""
    meta: dict[str, str] = {}
    if not html:
        return meta
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html[:20000], "html.parser")
        # 寻找发布时间
        for prop in ("article:published_time", "og:published_time", "pubdate", "publishdate", "date"):
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                val = str(tag["content"]).strip()
                for p in _DATE_PATTERNS:
                    m = p.search(val)
                    if m:
                        meta["date"] = m.group(1).replace("年", "-").replace("月", "-")[:10]
                        break
                if "date" in meta:
                    break

        # 寻找作者/来源
        for prop in ("author", "og:site_name", "publisher"):
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                meta["author"] = str(tag["content"]).strip()[:32]
                break
    except Exception:
        pass
    return meta


async def _fetch_and_extract_body(client: httpx.AsyncClient, url: str, timeout: float = 3.5) -> tuple[str, dict[str, str]]:
    """快速抓取单个网页正文并用 trafilatura 抽取，返回 (正文, 元数据)。"""
    try:
        host = urlsplit(url).netloc
        resp = await client.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": f"https://{host}/",
            },
        )
        if resp.status_code != 200 or not resp.content:
            return "", {}

        html_text = resp.text
        meta = _extract_meta(html_text)

        import trafilatura

        text = trafilatura.extract(
            html_text,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        return (text or "").strip(), meta
    except Exception:  # noqa: BLE001
        return "", {}


async def hydrate_results_async(
    results: list[Result],
    max_items: int = 5,
    timeout: float = 4.0,
) -> list[Result]:
    """并发对排在前列、正文过短的结果抓取并补全完整正文与元数据。"""
    if not results or max_items <= 0:
        return results

    targets: list[tuple[int, Result]] = []
    for idx, r in enumerate(results[:max_items]):
        if len(r.content or "") < 400 and r.url.startswith("http"):
            targets.append((idx, r))

    if not targets:
        return results

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(limits=limits, verify=False) as client:
        tasks = [
            _fetch_and_extract_body(client, item.url, timeout=timeout)
            for _, item in targets
        ]
        extracted = await asyncio.gather(*tasks, return_exceptions=True)

        for (idx, r), res in zip(targets, extracted):
            body, meta = (res, {}) if isinstance(res, str) else (res[0], res[1]) if (isinstance(res, tuple) and len(res) == 2) else ("", {})
            if len(body) >= 300:
                r.content = body
                r.summary = body[:300]
                if r.authority <= 1:
                    r.authority = 2
                if meta.get("date") and not r.date:
                    r.date = meta["date"]
                if meta.get("author") and not r.site_name:
                    r.site_name = meta["author"]

    return results


def hydrate_rows(rows: list[dict], max_items: int = 5, timeout: float = 4.0) -> list[dict]:
    """对已转为字典行的数据执行深度正文补全与元数据增强。"""
    if not rows:
        return rows

    targets = []
    for idx, row in enumerate(rows[:max_items]):
        body = row.get("body") or ""
        text = row.get("text") or ""
        url = row.get("url") or ""
        if len(body) < 400 and len(text) < 400 and url.startswith("http"):
            targets.append((idx, url))

    if not targets:
        return rows

    async def _run():
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        async with httpx.AsyncClient(limits=limits, verify=False) as client:
            tasks = [_fetch_and_extract_body(client, url, timeout=timeout) for _, url in targets]
            return await asyncio.gather(*tasks, return_exceptions=True)

    try:
        extracted = asyncio.run(_run())
        for (idx, _), res in zip(targets, extracted):
            text, meta = (res, {}) if isinstance(res, str) else (res[0], res[1]) if (isinstance(res, tuple) and len(res) == 2) else ("", {})
            if len(text) >= 300:
                rows[idx]["body"] = text
                rows[idx]["text"] = text
                if rows[idx].get("authority", 0) <= 1:
                    rows[idx]["authority"] = 2
                if meta.get("date") and not rows[idx].get("date"):
                    rows[idx]["date"] = meta["date"]
                if meta.get("author") and not rows[idx].get("author"):
                    rows[idx]["author"] = meta["author"]
    except Exception:  # noqa: BLE001
        pass

    return rows
