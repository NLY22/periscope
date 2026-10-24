"""并发采集：把串行的 153 秒压到接近最慢单源。

实测串行耗时（15 个源，话题「钠离子电池」）：
    合计 153.2 秒，其中 googlenews_cn / googlenews_en 各超时 40 秒
    （这两个源在话题模式下基本拿不到东西，纯浪费 80 秒）

并发后总耗时 ≈ max(单源耗时)，即 bili_search 的 26 秒。
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import asyncio
import io
import time

OUT = paths.REPORT_DIR / '.probe_concurrent.txt'

import httpx

from collectors import collector

TOPIC = "钠离子电池"

# 话题模式下值得跑的源。去掉两个超时的 googlenews：
# 它们在「限定话题」场景下返回 0 条且各耗 40 秒，是纯损耗。
SOURCES = [
    "weibo_search", "zhihu_search", "zhihu_topic", "bili_search",
    "csdn_search", "csdn_ask", "juejin_search", "juejin_column",
    "v2ex_search", "hupu_search", "toutiao_search", "wechat_search",
    "youtube_cn",
]


async def run_one(cli, src, topic: str):
    import dataclasses

    s = dataclasses.replace(src, queries=(topic,), max_pages=2)
    t0 = time.monotonic()
    try:
        rows, st = await asyncio.wait_for(collector.collect(cli, s), timeout=40)
        return s.key, len(rows), time.monotonic() - t0, st.error or ""
    except asyncio.TimeoutError:
        return s.key, 0, time.monotonic() - t0, "超时"
    except Exception as exc:  # noqa: BLE001
        return s.key, 0, time.monotonic() - t0, type(exc).__name__


async def main() -> int:
    by_key = {s.key: s for s in collector.CATALOG}
    picked = [by_key[k] for k in SOURCES if k in by_key]

    lines = []
    async with httpx.AsyncClient(
            headers=collector.UA, timeout=30, follow_redirects=True) as cli:
        t0 = time.monotonic()
        results = await asyncio.gather(
            *(run_one(cli, s, TOPIC) for s in picked))
        total = time.monotonic() - t0

    results.sort(key=lambda r: -r[2])
    lines.append("%-18s %-6s %-8s %s" % ("源", "条数", "耗时", "备注"))
    lines.append("-" * 60)
    for key, n, el, err in results:
        lines.append("%-18s %-6d %-8s %s" % (key, n, "%.1fs" % el, err))
    lines.append("-" * 60)
    lines.append("并发总耗时 %.1fs   条数合计 %d"
                 % (total, sum(r[1] for r in results)))
    lines.append("（串行需要 %.1fs）" % sum(r[2] for r in results))

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
