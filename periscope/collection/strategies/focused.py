"""Concurrent topic collection with per-source and overall time budgets."""

from __future__ import annotations

from pathlib import Path
import asyncio
import dataclasses
import sys
import time

import httpx

from periscope.collection.transport import client as collector
from periscope.runtime import paths


# 话题模式下跑的源（实测 545 条 / 26 秒）。
#
# 不含 googlenews_cn / googlenews_en：话题模式下各返回 0 条且各耗 40 秒，
# 是纯损耗。它们适合「按月批量回溯」，不适合现场回答。

ROOT = paths.ROOT

TOPIC_SOURCES = [
    "weibo_search", "zhihu_search", "zhihu_topic", "bili_search",
    "csdn_search", "csdn_ask", "juejin_search", "juejin_column",
    "v2ex_search", "hupu_search", "toutiao_search", "wechat_search",
    "youtube_cn",
]

# 单源超时：B站 是最慢的（2 页 26 秒），其他都在 10 秒内。
DEFAULT_TIMEOUT = 30.0


async def _one(cli, src, topic: str, pages: int, timeout: float):
    s = dataclasses.replace(src, queries=(topic,), max_pages=pages)
    t0 = time.monotonic()
    try:
        rows, st = await asyncio.wait_for(
            collector.collect(cli, s), timeout=timeout)
        return s.key, list(rows), time.monotonic() - t0, st.error or ""
    except asyncio.TimeoutError:
        return s.key, [], time.monotonic() - t0, "超时>%.0fs" % timeout
    except Exception as exc:  # noqa: BLE001
        return s.key, [], time.monotonic() - t0, type(exc).__name__


async def collect_topic_async(
    topic: str,
    sources: list[str] | None = None,
    pages: int = 2,
    timeout: float = DEFAULT_TIMEOUT,
    budget: float = 0.0,
) -> tuple[list[dict], dict]:
    """并发采集一个话题，返回 (行, 统计)。

    行的形状与 `collector` 一致，额外补 `source` 与 `kind` 供入库使用。

    `timeout` 是**单源**上限，`budget` 是**整体**上限（0 = 不限）。

    为什么两个都要：并发之后整体耗时由最慢的源决定，而单源超时管不到
    「六个源各 25 秒是否可接受」。`topic.py --budget` 一直是这个意思，
    但它只被传给浏览器采集的子进程，HTTP 层从来收不到 —— 也就是说
    默认 fast 路径上这个参数**收下了、什么也没做**。

    到达 budget 之后不是抛错，而是取消还没回来的源并保留已采到的：
    「慢源没赶上」和「这次失败」是两件事，前者应该表现为条数少一点，
    而不是整轮没有结果。
    """
    by_key = {s.key: s for s in collector.CATALOG}
    keys = sources or TOPIC_SOURCES
    picked = [by_key[k] for k in keys if k in by_key]

    # Sources whose page renders client-side. `collect()` can only read the HTML
    # it is handed, so these return zero rows every time while still costing
    # requests and retries (虎扑 answers 405 intermittently, which the retry
    # logic interprets as throttling and retries three times).
    #
    # Skipping them is not the same as hiding them: they are reported in
    # `skipped_browser`, so a caller can see that the source exists and why it
    # produced nothing. Removing them from the catalog would be worse - the key
    # is what a later browser pass would collect under.
    http_sources = [s for s in picked if not s.needs_browser]
    skipped_browser = [s.key for s in picked if s.needs_browser]

    t0 = time.monotonic()
    rows: list[dict] = []
    per: list[tuple[str, int, float, str]] = []

    async with httpx.AsyncClient(
            headers=collector.UA, timeout=25, follow_redirects=True) as cli:
        tasks = [asyncio.ensure_future(_one(cli, s, topic, pages, timeout))
                 for s in http_sources]
        if budget and budget > 0:
            done, pending = await asyncio.wait(tasks, timeout=budget)
            for task in pending:
                task.cancel()
            # Let the cancellations settle so the client closes cleanly and no
            # task is left warning at interpreter shutdown.
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            results = [t.result() for t in done if not t.cancelled()
                       and t.exception() is None]
            dropped = len(pending)
        else:
            results = list(await asyncio.gather(*tasks))
            dropped = 0

    for key, got, el, err in results:
        per.append((key, len(got), el, err))
        src = by_key.get(key)
        for r in got:
            r.setdefault("source", key)
            r.setdefault("kind", src.kind if src else "community")
        rows.extend(got)

    per.sort(key=lambda x: -x[2])
    # Sources that did not make the budget are reported separately from sources
    # that failed: "we ran out of time" is not "the endpoint refused". Browser
    # sources are a third category again - never attempted here by design.
    ran_keys = {p[0] for p in per}
    attempted = {s.key for s in http_sources}
    over_budget = [s.key for s in http_sources if s.key not in ran_keys]
    stat = {
        "elapsed": time.monotonic() - t0,
        "total": len(rows),
        "per": per,
        "sources": len(picked),
        "attempted": len(attempted),
        "failed": [p[0] for p in per if p[3]],
        "over_budget": over_budget,
        "skipped_browser": skipped_browser,
        "budget": budget,
    }
    return rows, stat


def collect_topic(topic: str, **kw) -> tuple[list[dict], dict]:
    """同步包装。"""
    return asyncio.run(collect_topic_async(topic, **kw))


if __name__ == "__main__":
    # `collectors.collector` 在 import 时会把 sys.stdout 换成自己的
    # UTF-8 wrapper，之后再包一层或直接 print 都会撞「I/O operation on
    # closed file」。所以结果写文件，并用 os.write 直接写 fd 回显。
    import os

    topic = sys.argv[1] if len(sys.argv) > 1 else "人形机器人"
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    budget = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

    rows, st = collect_topic(topic, pages=pages, budget=budget)
    lines = ["话题: %s   源: %d   条数: %d   耗时: %.1fs"
             % (topic, st["sources"], st["total"], st["elapsed"])]
    if st.get("over_budget"):
        lines.append("  超出预算未返回: %s" % ", ".join(st["over_budget"]))
    for key, n, el, err in st["per"]:
        lines.append("  %-18s %4d  %5.1fs  %s" % (key, n, el, err))

    text = "\n".join(lines)
    (paths.REPORT_DIR / ".fanout_out.txt").write_text(text, encoding="utf-8")
    os.write(1, (text + "\n").encode("utf-8", "replace"))
