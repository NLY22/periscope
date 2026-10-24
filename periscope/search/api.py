"""Rate-limited search/fetch API client and bounded query expansion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import asyncio
import sys
import time

import httpx

from periscope.runtime import paths


SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"

# 文档明确的限速：Search 与 Fetch 各自 5 QPS，按账号算
QPS = 5
MIN_INTERVAL = 1.0 / QPS

# 实测：count 必须 1-50，100 会返回 400
MAX_COUNT = 50


@dataclass
class Result:
    """一条搜索结果，字段名对齐我们自己的 items 形状。"""

    title: str = ""
    url: str = ""
    site_name: str = ""
    snippet: str = ""
    summary: str = ""
    content: str = ""
    date: str = ""
    rerank_score: float = 0.0
    authority: int = -1

    @property
    def body_chars(self) -> int:
        return len(self.content or "")

    def to_row(self) -> dict[str, Any]:
        """转成与采集行一致的形状，便于统一入库。"""
        return {
            "source": "dsapi",
            "kind": "media",
            "title": self.title,
            "url": self.url,
            "date": self.date,
            "author": self.site_name,
            "text": self.content or self.summary or self.snippet,
            "body": self.content,
            "authority": self.authority,
            "rerank": self.rerank_score,
        }


@dataclass
class SearchResponse:
    results: list[Result] = field(default_factory=list)
    elapsed: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def rows(self) -> list[dict]:
        return [r.to_row() for r in self.results]


class _RateLimiter:
    """账号级 5 QPS。并发调用时排队，避免撞 429。"""

    def __init__(self, interval: float = MIN_INTERVAL):
        self.interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.interval:
                await asyncio.sleep(self.interval - delta)
            self._last = time.monotonic()


_limiter: _RateLimiter | None = None


def _get_limiter() -> _RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = _RateLimiter()
    return _limiter


def _headers() -> dict[str, str]:
    k = paths.deepseek_key()
    if not k:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY：设置环境变量，或写入 %s"
            % paths.DEEPSEEK_KEY_FILE)
    return {"Authorization": "Bearer " + k, "Content-Type": "application/json"}


def available() -> bool:
    """有没有可用的 key。调用方据此决定是否走 API 路径。"""
    return bool(paths.deepseek_key())


def _parse(results: list) -> list[Result]:
    out = []
    for x in results or []:
        if not isinstance(x, dict):
            continue
        out.append(Result(
            title=x.get("title") or "",
            url=x.get("url") or "",
            site_name=x.get("site_name") or "",
            snippet=x.get("snippet") or "",
            summary=x.get("summary") or "",
            content=x.get("content") or "",
            date=x.get("date") or "",
            rerank_score=float(x.get("rerank_score") or 0),
            authority=int(x.get("authority") if x.get("authority") is not None else -1),
        ))
    return out


async def search_async(
    query: str,
    count: int = 20,
    need_content: bool = True,
    ori_question: str = "",
    timeout: float = 90.0,
) -> SearchResponse:
    """单次搜索。"""
    count = max(1, min(int(count), MAX_COUNT))
    body: dict[str, Any] = {
        "query": query,
        "count": count,
        "need_content": bool(need_content),
    }
    if ori_question:
        body["ori_question"] = ori_question[:128]

    await _get_limiter().wait()
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as cli:
            r = await cli.post(SEARCH_URL, headers=_headers(), json=body)
    except Exception as exc:  # noqa: BLE001
        return SearchResponse(elapsed=time.monotonic() - t0,
                              error="%s: %s" % (type(exc).__name__, exc))

    el = time.monotonic() - t0
    if r.status_code != 200:
        return SearchResponse(elapsed=el,
                              error="HTTP %d %s" % (r.status_code, r.text[:160]))
    try:
        data = r.json()
    except Exception:
        return SearchResponse(elapsed=el, error="响应不是 JSON")

    return SearchResponse(results=_parse(data.get("results")), elapsed=el)


def search(query: str, count: int = 20, need_content: bool = True,
           ori_question: str = "") -> SearchResponse:
    """同步包装，供非 async 调用方使用。"""
    return asyncio.run(search_async(
        query, count=count, need_content=need_content, ori_question=ori_question))


async def search_multi(queries: list[str], count: int = 20,
                       need_content: bool = True) -> list[SearchResponse]:
    """并行搜索多个查询（内部按 5 QPS 排队）。"""
    return list(await asyncio.gather(
        *(search_async(q, count=count, need_content=need_content)
          for q in queries)))


# ---------------------------------------------------------------- 广度检索
# 单次 Search 的 `count` 上限是 50（100 会返回 400）。这是接口硬限制，不是
# 我们不愿多取 —— 所以「一次查询 = 最多 50 条」是这条层的地板。
#
# 但同一个话题换几种问法，返回的集合差别很大。实测「固态电池」：
#
#     变体                          新增唯一结果
#     (base)                              50
#     固态电池 2026                       +39
#     固态电池 最新进展                    +33
#     固态电池 技术                        +27
#     固态电池 site:news.cn               +49
#     固态电池 site:people.com.cn         +47
#     固态电池 量产                        +20
#     ------------------------------------------
#     单查询 50  →  7 变体合并 266（5.3 倍）
#
# 且质量不降：266 条里 264 条带正文（99%），中位 2,118 字，92 个独立发布者。
# 原因是变体打散的是**排序**，不是相关性 —— 单次查询的 50 条是同一个
# rerank 视角的前 50，换问法就换了一个视角，捞起的是原本排在 51 名之后
# 但同样相关的内容。
#
# 变体分三类，各有明确作用：
#   - 时效类（最新 / 当前年份）：主排序偏爱权威旧文，时效词把新的挤上来
#   - 站点类（site:权威站）：主排序会低估某些垂类/官方站点
#   - 深度类（分析 / 报告）：找到综述型长文，适合做研究底料
#
# 注意 `技术` / `量产` 这类词是**领域相关**的，通用化会伤到别的主题
# （问一家公司名时加「量产」没有意义）。所以下面只保留跨主题成立的变体。


def topic_variants(topic: str, depth: int = 3,
                   year: str = "") -> list[str]:
    """Expand one topic into several phrasings, cheapest-first.

    `depth` is a count, not a mode: 1 means "just the topic" (current
    behaviour), and the caller decides how many it can afford. Ordered so that a
    smaller depth is a prefix of a larger one - that makes the cost predictable
    and means a truncated run still did the most valuable queries.

    The site-limited variants are the highest-yield ones measured (+49 and +47
    unique results out of 50), because they reach outlets the general ranking
    under-weights. They are included before the generic suffix variants for that
    reason.

    `year` defaults to the current year; passed in so a test can pin it.
    """
    topic = (topic or "").strip()
    if not topic:
        return []
    if not year:
        import datetime as _dt

        year = str(_dt.datetime.now().year)

    ordered = [
        topic,                                  # the baseline: always present
        "%s site:news.cn" % topic,              # 新华社/新华网
        "%s site:people.com.cn" % topic,        # 人民网
        "%s 最新进展" % topic,
        "%s %s" % (topic, year),
        "%s site:thepaper.cn" % topic,          # 澎湃
        "%s 深度分析" % topic,
        "%s site:gov.cn" % topic,               # 政策原文
        "%s 研究报告" % topic,
    ]
    # De-duplicate while keeping order: a topic that already contains a suffix
    # must not produce the same query twice and pay for it twice.
    out, seen = [], set()
    for q in ordered[:max(1, depth)]:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


async def search_wide(
    topic: str,
    count: int = 50,
    depth: int = 3,
    need_content: bool = True,
) -> SearchResponse:
    """Search one topic through several phrasings and merge the union.

    Result identity is the URL. When the same URL appears under two variants, the
    first occurrence wins - which, given `topic_variants`' ordering, means the
    plain-query hit. That is deliberate: the baseline ranking's metadata
    (authority / rerank) is the one a caller expects to see, and a `site:` hit
    for the same URL carries no better information.

    A phrase-level fallback: if *every* variant fails, the returned response
    carries the last error rather than an empty success. An empty success and a
    failed request need different handling by the caller, and collapsing them
    is how "the network is down" becomes "there is no such topic".

    Measured: 7 variants -> 266 unique results in 5.4s, 99% carrying body text.
    """
    queries = topic_variants(topic, depth)
    if not queries:
        return SearchResponse(error="empty topic")

    responses = await search_multi(queries, count=count, need_content=need_content)

    merged: dict[str, Result] = {}
    errors: list[str] = []
    elapsed = 0.0
    for q, resp in zip(queries, responses):
        elapsed = max(elapsed, resp.elapsed)
        if not resp.ok:
            errors.append("%s: %s" % (q, resp.error))
            continue
        for result in resp.results:
            key = (result.url or "").strip() or ("title:" + result.title.strip())
            if not key or key in merged:
                continue
            merged[key] = result

    if not merged and errors:
        return SearchResponse(elapsed=elapsed, error="; ".join(errors)[:200])

    return SearchResponse(results=list(merged.values()), elapsed=elapsed)


async def fetch_async(url: str, timeout: float = 60.0) -> tuple[str, str, str]:
    """抓单个网页，返回 (title, content_markdown, error)。

    实测耗时 5.34 秒，比 Search 慢 5 倍；只在确实需要某个特定页面时用。
    """
    await _get_limiter().wait()
    try:
        async with httpx.AsyncClient(timeout=timeout) as cli:
            r = await cli.post(FETCH_URL, headers=_headers(), json={"url": url})
    except Exception as exc:  # noqa: BLE001
        return "", "", "%s: %s" % (type(exc).__name__, exc)
    if r.status_code != 200:
        return "", "", "HTTP %d" % r.status_code
    try:
        d = r.json()
    except Exception:
        return "", "", "响应不是 JSON"
    return d.get("title") or "", d.get("content") or "", ""


def fetch(url: str) -> tuple[str, str, str]:
    return asyncio.run(fetch_async(url))


# ---------------------------------------------------------------- 便捷入口
def search_topic(topic: str, count: int = 40) -> SearchResponse:
    """按话题搜索 —— 单次查询。

    这是**窄**入口，保留它有两个用处：`--api-count 1` 这类只需要一次查询的
    场景，以及作为 `search_wide` 的基线对照。

    早先的注释说「同一话题多查几次的收益很小（前三名高度重叠）」，那是
    只看前三名得出的结论。实测唯一结果数：单查询 50 → 7 变体 266。
    前三名确实高度重叠，但**第 51 名之后不重叠**，而那正是广度所在。
    要广度请用 `search_topic_wide`。
    """
    return search(topic, count=count, need_content=True)


def search_topic_wide(topic: str, count: int = 50, depth: int = 3) -> SearchResponse:
    """按话题搜索 —— 多变体合并。同步包装，供 `topic.py` 使用。

    `depth` = 用几种问法（1 = 与 `search_topic` 等价）。实测每多一问法
    约多 0.8 秒，换来 20-50 条新的唯一结果，且都带正文。
    """
    return asyncio.run(search_wide(topic, count=count, depth=depth,
                                   need_content=True))


if __name__ == "__main__":
    from periscope.runtime.console import force_utf8_output

    force_utf8_output()
    topic = sys.argv[1] if len(sys.argv) > 1 else "人形机器人"
    print("查询:", topic)
    if not available():
        print("没有 API key")
        raise SystemExit(1)
    r = search_topic(topic, count=10)
    print("%d 条  %.2fs  %s" % (len(r.results), r.elapsed, r.error))
    for x in r.results[:5]:
        print("  [a=%s r=%.2f] %s" % (x.authority, x.rerank_score, x.title[:52]))
        print("       %s  %d 字" % (x.site_name[:16], x.body_chars))
