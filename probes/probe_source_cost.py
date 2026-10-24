"""测每个「话题型」源单次采集的耗时与产出。

用来决定：哪些源值得留在默认路径里，哪些交给 API。
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths


import asyncio
import io
import time

# collector 在 import 时会把 sys.stdout 换成自己的 UTF-8 wrapper，
# 之后再用 wrapper 包一次就会撞上已关闭的流。所以这里不碰 stdout，
# 结果写文件再回显。
OUT = paths.REPORT_DIR / '.probe_source_cost.txt'

import httpx

from collectors import collector

TOPIC = "钠离子电池"

# 当前 topic 路径在用的 15 个源
CANDIDATES = [
    "weibo_search", "zhihu_search", "zhihu_topic", "bili_search",
    "csdn_search", "csdn_ask", "juejin_search", "juejin_column",
    "v2ex_search", "hupu_search", "toutiao_search", "wechat_search",
    "youtube_cn", "googlenews_cn", "googlenews_en",
]

by_key = {s.key: s for s in collector.CATALOG}


async def main() -> int:
    rows_stat = []
    async with httpx.AsyncClient(
            headers=collector.UA, timeout=30, follow_redirects=True) as cli:
        for key in CANDIDATES:
            src = by_key.get(key)
            if not src:
                rows_stat.append((key, 0, 0.0, "源不存在"))
                continue
            import dataclasses
            src = dataclasses.replace(src, queries=(TOPIC,), max_pages=2)
            t0 = time.monotonic()
            try:
                rows, st = await asyncio.wait_for(
                    collector.collect(cli, src), timeout=40)
                el = time.monotonic() - t0
                err = st.error or ""
                rows_stat.append((key, len(rows), el, err[:40]))
            except asyncio.TimeoutError:
                rows_stat.append((key, 0, time.monotonic() - t0, "超时>40s"))
            except Exception as exc:  # noqa: BLE001
                rows_stat.append((key, 0, time.monotonic() - t0,
                                  type(exc).__name__))

    lines = []
    lines.append("%-18s %-6s %-8s %s" % ("源", "条数", "耗时", "备注"))
    lines.append("-" * 62)
    total = 0.0
    for key, n, el, err in rows_stat:
        total += el
        lines.append("%-18s %-6d %-8s %s" % (key, n, "%.1fs" % el, err))
    lines.append("-" * 62)
    lines.append("%-18s %-6d %-8s" % ("合计串行",
                                      sum(r[1] for r in rows_stat),
                                      "%.1fs" % total))
    lines.append("%-18s %-6s %-8s" % ("最长单个", "",
                                      "%.1fs" % max((r[2] for r in rows_stat),
                                                    default=0)))
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
