"""按任意话题跑 HTTP 搜索源。

`collector.py` 的 queries 是写死的词表（CN_TERMS 等），适合日常巡检；
这里反过来：给定一个话题，只跑「接受任意查询词」的源，每个源问同一个
话题，把结果写到单独的分片文件，不碰已有的大文件。

用法：
    python collect_http.py --topic "室温超导"
    python collect_http.py --topic "室温超导" --sources weibo_search zhihu_search
"""

from __future__ import annotations

from pathlib import Path
import argparse
import asyncio
import json
import sys
import time

from periscope.collection.transport import client as collector
from periscope.runtime import paths


# 能在「任意话题」下工作的源。选中的标准是实测过：
# 输入一个没见过的词，仍然返回结构化结果。
#
# 没列进来的：
#   hn_ai / hn_cn / hn_comments   词表驱动，且是英文技术圈
#   arxiv_*                       学科分类，不是话题检索
#   gh_* / npm / pypi             包名检索，不是话题
#   *_hot / *_rank / *_popular    榜单，不接受查询词

ROOT = paths.ROOT

TOPIC_SOURCES = [
    "weibo_search",
    "zhihu_search",
    "zhihu_topic",
    "bili_search",
    "csdn_search",
    "csdn_ask",
    "juejin_search",
    "juejin_column",
    "v2ex_search",
    "hupu_search",
    "toutiao_search",
    "wechat_search",
    "youtube_cn",
    "googlenews_cn",
    "googlenews_en",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="按话题跑 HTTP 搜索源")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--sources", nargs="*", default=None)
    ap.add_argument("--max-pages", type=int, default=0,
                    help="覆盖每个源的页数上限（0 = 用默认 2 页）")
    ap.add_argument("--budget", type=float, default=90.0,
                    help="总时间预算（秒）。超预算后跳过剩余源，已采到的保留")
    ap.add_argument("--per-source-timeout", type=float, default=25.0,
                    help="单个源的硬超时（秒）。B站这类 3 秒/请求的源"
                         "两页就要 6 秒以上，再叠加重试会独自吃掉整个预算")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    topic = args.topic.strip()
    want = args.sources or TOPIC_SOURCES
    by_key = {s.key: s for s in collector.CATALOG}

    picked = []
    for k in want:
        s = by_key.get(k)
        if not s:
            continue
        # 用 dataclasses.replace 覆盖 queries 与 max_pages
        import dataclasses

        # 「任意话题」场景下页数必须收窄。默认页数是给全量巡检用的
        # （B站 10 页 × 28 词 = 280 请求），在这里会直接撞破时间预算。
        # 两页通常 40-100 条，够回答「这个话题有没有料」。
        pages = args.max_pages or 2
        changes = {"queries": (topic,), "max_pages": pages}
        picked.append(dataclasses.replace(s, **changes))

    # 快源优先：先让便宜的源把结果交出来，预算用尽就停。
    # 顺序按请求间隔（小的在前），这样 B站/掘金这类慢源排在最后。
    picked.sort(key=lambda s: s.min_interval)

    out_path = Path(args.out) if args.out else (
        paths.COLLECT_DIR / "http_items.topic.json")
    async def run() -> int:
        import httpx

        results = []
        stats = []
        started = time.monotonic()
        async with httpx.AsyncClient(
            headers=collector.UA, timeout=30, follow_redirects=True
        ) as client:
            for src in picked:
                if (time.monotonic() - started > args.budget) and results:
                    stats.append((src.key, 0,
                                  "跳过（已用 %.0fs 超出预算 %.0fs）"
                                  % (time.monotonic() - started, args.budget)))
                    continue
                t0 = time.monotonic()
                try:
                    rows, st = await asyncio.wait_for(
                        collector.collect(client, src),
                        timeout=args.per_source_timeout)
                except asyncio.TimeoutError:
                    stats.append((src.key, 0,
                                  "超时（>%.0fs）" % args.per_source_timeout))
                    continue
                except Exception as exc:  # noqa: BLE001
                    stats.append((src.key, 0, "%s" % type(exc).__name__))
                    continue
                # 补上 source/kind 字段，ingest 需要
                for r in rows:
                    r.setdefault("source", src.key)
                    r.setdefault("kind", src.kind)
                results.extend(rows)
                note = "%.0fs" % (time.monotonic() - t0)
                if st.error:
                    note += " " + st.error
                stats.append((src.key, len(rows), note))

        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

        total_s = time.monotonic() - started
        lines = ["topic: %s   耗时 %.0fs   预算 %.0fs"
                 % (topic, total_s, args.budget), ""]
        for key, n, err in stats:
            lines.append("  %-20s %5d %s" % (key, n, err))
        lines.append("")
        lines.append("total: %d -> %s" % (len(results), out_path.name))
        (paths.REPORT_DIR / ".collect_http.txt").write_text(
            "\n".join(lines), encoding="utf-8")
        return len(results)

    n = asyncio.run(run())
    # collector 会重绑 stdout，这里用文件写结果并回显
    try:
        txt = (paths.REPORT_DIR / ".collect_http.txt").read_text(encoding="utf-8")
        print(txt)
    except Exception:
        print("total: %d" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
