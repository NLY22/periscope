"""抖音搜索采集器（浏览器拦截响应包）。

页面负责正常的搜索请求与平台校验，本适配读取页面收到的结果。
默认使用独立的无头浏览器和持久化会话；按结果到达继续处理，不固定等待。

用法：
    python collect_douyin.py --queries 人形机器人 固态电池 --scrolls 6
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import re
import sys
import urllib.parse

from periscope.collection.platforms.browser import browser_session, ensure_env, run_collector, wait_for_items, write_checkpoint
from periscope.runtime import paths


ROOT = paths.ROOT

PROFILES = paths.PROFILES
OUT_DEFAULT = paths.COLLECT_DIR / "ugc_search.douyin.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DEFAULT_QUERIES = [
    "人形机器人", "具身智能", "大模型", "AI 应用", "智能体",
    "算力", "芯片", "英伟达", "DeepSeek", "通义千问",
    "豆包", "Kimi", "文心一言", "智谱", "MiniMax",
    "AI 副业", "AI 失业", "本地部署", "提示词", "AI 翻车",
]

# 抖音搜索响应里，能确定是内容列表的 URL 特征
SEARCH_HINTS = (
    "/aweme/v1/web/general/search/single/",
    "/aweme/v1/web/search/item/",
    "/aweme/v1/web/general/search/stream/",
)


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&amp;", "&").replace("&nbsp;", " ")
    return " ".join(s.split())


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", nargs="*", default=None)
    ap.add_argument("--limit-queries", type=int, default=0)
    ap.add_argument("--scrolls", type=int, default=6)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--headed", action="store_true", default=False,
                    help="显示浏览器窗口（默认不显示）")
    return ap.parse_args()


def walk_awemes(obj, out: list) -> None:
    """在任意嵌套里找 aweme_info / aweme_list 里的条目。"""
    if isinstance(obj, dict):
        # 单个 aweme
        if "aweme_id" in obj and ("desc" in obj or "aweme_id" in obj):
            out.append(obj)
            return
        for k, v in obj.items():
            if k in ("aweme_info", "aweme_detail") and isinstance(v, dict):
                out.append(v)
            elif isinstance(v, (dict, list)):
                walk_awemes(v, out)
    elif isinstance(obj, list):
        for it in obj:
            walk_awemes(it, out)


def main() -> int:
    args = parse_args()
    out = Path(getattr(args, "out", OUT_DEFAULT))

    queries = args.queries or DEFAULT_QUERIES
    if args.limit_queries:
        queries = queries[: args.limit_queries]

    prof = PROFILES / "douyin"
    rows: list[dict] = []
    seen: set[str] = set()

    with browser_session(prof, headed=getattr(args, "headed", False), user_agent=UA, prepare=ensure_env,
                         viewport={"width": 1440, "height": 950}) as ctx:
        write_checkpoint(out, [])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        bucket: list[dict] = []

        def on_resp(r) -> None:
            u = r.url
            if not any(h in u for h in SEARCH_HINTS):
                return
            ct = (r.headers or {}).get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = r.json()
            except Exception:
                return
            found: list[dict] = []
            walk_awemes(body, found)
            bucket.extend(found)

        page.on("response", on_resp)

        for qi, q in enumerate(queries, 1):
            bucket.clear()
            url = ("https://www.douyin.com/search/"
                   + urllib.parse.quote(q) + "?type=general")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=50000)
            except Exception:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=40000)
                except Exception:
                    pass
            wait_for_items(page, lambda: len(bucket), timeout_ms=8000)

            before = len(rows)
            stall = 0
            for scroll in range(max(0, args.scrolls) + 1):
                if scroll:
                    before_response = len(bucket)
                    page.mouse.wheel(0, 3000)
                    wait_for_items(page, lambda: len(bucket), previous=before_response, timeout_ms=2800)

                added = 0
                for a in list(bucket):
                    aid = str(a.get("aweme_id") or "")
                    desc = clean(a.get("desc") or "")
                    if not aid or not desc or len(desc) < 4:
                        continue
                    if aid in seen:
                        continue
                    seen.add(aid)
                    author = ((a.get("author") or {}).get("nickname") or "")
                    stats = a.get("statistics") or {}
                    ct_epoch = a.get("create_time")
                    rows.append({
                        "platform": "douyin",
                        "platform_name": "抖音搜索",
                        "kind": "视频",
                        "title": desc[:120],
                        "url": "https://www.douyin.com/video/%s" % aid,
                        "text": desc[:400],
                        "author": clean(author),
                        "date": str(ct_epoch or ""),
                        "likes": str(stats.get("digg_count") or ""),
                        "query": q,
                    })
                    added += 1

                if added:
                    write_checkpoint(out, rows)
                if added == 0:
                    stall += 1
                    if stall >= 3:
                        break
                else:
                    stall = 0

            print("  [%d/%d] %-14s +%-4d 累计 %d" % (
                qi, len(queries), q, len(rows) - before, len(rows)), flush=True)

    write_checkpoint(out, rows)
    print()
    print("queries: %d   items: %d   -> %s" % (len(queries), len(rows), out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_collector(main))
